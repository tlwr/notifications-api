import json
import logging

from time import monotonic
from requests import request, RequestException, Session
from requests.adapters import HTTPAdapter

from app.clients.sms import (SmsClient, SmsClientResponseException)

logger = logging.getLogger(__name__)

# Firetext will send a delivery receipt with three different status codes.
# The `firetext_response` maps these codes to the notification statistics status and notification status.
# If we get a pending (status = 2) delivery receipt followed by a declined (status = 1) delivery receipt we will set
# the notification status to temporary-failure rather than permanent failure.
#  See the code in the notification_dao.update_notifications_status_by_id
firetext_responses = {
    '0': 'delivered',
    '1': 'permanent-failure',
    '2': 'pending'
}

firetext_codes = {
    # code '000' means 'No errors reported'
    '101': {'status': 'permanent-failure', 'reason': 'Unknown Subscriber'},
    '102': {'status': 'temporary-failure', 'reason': 'Absent Subscriber'},
    '103': {'status': 'temporary-failure', 'reason': 'Subscriber Busy'},
    '104': {'status': 'temporary-failure', 'reason': 'No Subscriber Memory'},
    '201': {'status': 'permanent-failure', 'reason': 'Invalid Number'},
    '301': {'status': 'permanent-failure', 'reason': 'SMS Not Supported'},
    '302': {'status': 'temporary-failure', 'reason': 'SMS Not Supported'},
    '401': {'status': 'permanent-failure', 'reason': 'Message Rejected'},
    '900': {'status': 'temporary-failure', 'reason': 'Routing Error'},
}


def get_firetext_responses(status, detailed_status_code=None):
    detailed_status = firetext_codes[detailed_status_code]['reason'] if firetext_codes.get(
        detailed_status_code, None
    ) else None
    return (firetext_responses[status], detailed_status)


def get_message_status_and_reason_from_firetext_code(detailed_status_code):
    return firetext_codes[detailed_status_code]['status'], firetext_codes[detailed_status_code]['reason']


class FiretextClientResponseException(SmsClientResponseException):
    def __init__(self, response, exception):
        status_code = response.status_code if response is not None else 504
        text = response.text if response is not None else "Gateway Time-out"
        self.status_code = status_code
        self.text = text
        self.exception = exception

    def __str__(self):
        return "Code {} text {} exception {}".format(self.status_code, self.text, str(self.exception))


class FiretextClient(SmsClient):
    '''
    FireText sms client.
    '''

    def init_app(self, current_app, statsd_client, *args, **kwargs):
        super(SmsClient, self).__init__(*args, **kwargs)
        self.current_app = current_app
        self.api_key = current_app.config.get('FIRETEXT_API_KEY')
        self.from_number = current_app.config.get('FROM_NUMBER')
        self.name = 'firetext'
        self.url = current_app.config.get('FIRETEXT_URL')
        self.statsd_client = statsd_client

        # this uses urllib3 under the hood to create a connection pool
        self.session = Session()
        self.session.mount('https://', HTTPAdapter(pool_maxsize=32))

    def get_name(self):
        return self.name

    def record_outcome(self, success, response):
        status_code = response.status_code if response else 503

        log_message = "API {} request {} on {} response status_code {}".format(
            "POST",
            "succeeded" if success else "failed",
            self.url,
            status_code
        )

        if success:
            self.current_app.logger.info(log_message)
            self.statsd_client.incr("clients.firetext.success")
        else:
            self.statsd_client.incr("clients.firetext.error")
            self.current_app.logger.error(log_message)

    def send_sms(self, to, content, reference, sender=None):

        data = {
            "apiKey": self.api_key,
            "from": self.from_number if sender is None else sender,
            "to": to.replace('+', ''),
            "message": content,
            "reference": reference
        }

        response = None
        start_time = monotonic()
        try:
            response = self.session.post(
                self.url,
                data=data,
                timeout=60
            )
            response.raise_for_status()
            try:
                json.loads(response.text)
                if response.json()['code'] != 0:
                    raise ValueError()
            except (ValueError, AttributeError) as e:
                self.record_outcome(False, response)
                raise FiretextClientResponseException(response=response, exception=e)
            self.record_outcome(True, response)
        except RequestException as e:
            self.record_outcome(False, e.response)
            raise FiretextClientResponseException(response=e.response, exception=e)
        finally:
            elapsed_time = monotonic() - start_time
            self.current_app.logger.info("Firetext request for {} finished in {}".format(reference, elapsed_time))
            self.statsd_client.timing("clients.firetext.request-time", elapsed_time)
            if response and hasattr(response, 'elapsed'):
                self.statsd_client.timing("clients.firetext.raw-request-time", response.elapsed.total_seconds())
        return response

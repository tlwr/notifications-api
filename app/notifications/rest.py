from datetime import datetime

from flask import (
    Blueprint,
    jsonify,
    request,
    current_app,
    url_for,
    json
)

from utils.template import Template
from app.clients.email.aws_ses import AwsSesResponses
from app import api_user, encryption, create_uuid, DATETIME_FORMAT, DATE_FORMAT
from app.authentication.auth import require_admin
from app.dao import (
    templates_dao,
    services_dao,
    notifications_dao
)
from app.notifications.process_client_response import (
    validate_callback_data,
    process_sms_client_response
)
from app.schemas import (
    email_notification_schema,
    sms_template_notification_schema,
    notification_status_schema,
    notifications_filter_schema
)
from app.celery.tasks import send_sms, send_email
from app.validation import allowed_send_to_number, allowed_send_to_email

notifications = Blueprint('notifications', __name__)

from app.errors import register_errors

register_errors(notifications)
aws_response = AwsSesResponses()


@notifications.route('/notifications/email/ses', methods=['POST'])
def process_ses_response():
    try:
        ses_request = json.loads(request.data)
        if 'Message' not in ses_request:
            current_app.logger.error(
                "SES callback failed: message missing"
            )
            return jsonify(
                result="error", message="SES callback failed: message missing"
            ), 400

        ses_message = json.loads(ses_request['Message'])

        if 'notificationType' not in ses_message:
            current_app.logger.error(
                "SES callback failed: notificationType missing"
            )
            return jsonify(
                result="error", message="SES callback failed: notificationType missing"
            ), 400

        try:
            aws_response.response_code_to_object(ses_message['notificationType'])
        except KeyError:
            current_app.logger.info(
                "SES callback failed: status {} not found.".format(ses_message['notificationType'])
            )
            return jsonify(
                result="error",
                message="SES callback failed: status {} not found".format(ses_message['notificationType'])
            ), 400

        notification_status = aws_response.response_code_to_notification_status(ses_message['notificationType'])
        notification_statistics_status = aws_response.response_code_to_notification_statistics_status(
            ses_message['notificationType']
        )

        try:
            source = ses_message['mail']['source']
            if is_not_a_notification(source):
                current_app.logger.info(
                    "SES callback for notify success:. source {} status {}".format(source, notification_status)
                )
                return jsonify(
                    result="success", message="SES callback succeeded"
                ), 200

            reference = ses_message['mail']['messageId']
            if notifications_dao.update_notification_status_by_reference(
                    reference,
                    notification_status,
                    notification_statistics_status
            ) == 0:
                current_app.logger.info(
                    "SES callback failed: notification not found. Status {}".format(notification_status)
                )
                return jsonify(
                    result="error",
                    message="SES callback failed: notification not found. Status {}".format(notification_status)
                ), 404

            if not aws_response.response_code_to_notification_success(ses_message['notificationType']):
                current_app.logger.info(
                    "SES delivery failed: notification {} has error found. Status {}".format(
                        reference,
                        aws_response.response_code_to_message(ses_message['notificationType'])
                    )
                )

            return jsonify(
                result="success", message="SES callback succeeded"
            ), 200

        except KeyError:
            current_app.logger.error(
                "SES callback failed: messageId missing"
            )
            return jsonify(
                result="error", message="SES callback failed: messageId missing"
            ), 400

    except ValueError as ex:
        current_app.logger.exception(
            "SES callback failed: invalid json {}".format(ex)
        )
        return jsonify(
            result="error", message="SES callback failed: invalid json"
        ), 400


def is_not_a_notification(source):
    invite_email = "{}@{}".format(
        current_app.config['INVITATION_EMAIL_FROM'],
        current_app.config['NOTIFY_EMAIL_DOMAIN']
    )
    if current_app.config['VERIFY_CODE_FROM_EMAIL_ADDRESS'] == source:
        return True
    if invite_email == source:
        return True
    return False


@notifications.route('/notifications/sms/mmg', methods=['POST'])
def process_mmg_response():
    client_name = 'MMG'
    data = json.loads(request.data)
    validation_errors = validate_callback_data(data=data,
                                               fields=['status', 'CID'],
                                               client_name=client_name)
    if validation_errors:
        [current_app.logger.info(e) for e in validation_errors]
        return jsonify(result='error', message=validation_errors), 400

    success, errors = process_sms_client_response(status=data.get('status'),
                                                  reference=data.get('CID'),
                                                  client_name='MMG')
    if errors:
        [current_app.logger.info(e) for e in errors]
        return jsonify(result='error', message=errors), 400
    else:
        return jsonify(result='success', message=success), 200


@notifications.route('/notifications/sms/firetext', methods=['POST'])
def process_firetext_response():
    client_name = 'Firetext'
    validation_errors = validate_callback_data(data=request.form,
                                               fields=['status', 'reference'],
                                               client_name=client_name)
    if validation_errors:
        current_app.logger.info(validation_errors)
        return jsonify(result='error', message=validation_errors), 400

    success, errors = process_sms_client_response(status=request.form.get('status'),
                                                  reference=request.form.get('reference'),
                                                  client_name=client_name)
    if errors:
        [current_app.logger.info(e) for e in errors]
        return jsonify(result='error', message=errors), 400
    else:
        return jsonify(result='success', message=success), 200


@notifications.route('/notifications/<uuid:notification_id>', methods=['GET'])
def get_notifications(notification_id):
    notification = notifications_dao.get_notification(api_user['client'], notification_id)
    return jsonify(data={"notification": notification_status_schema.dump(notification).data}), 200


@notifications.route('/notifications', methods=['GET'])
def get_all_notifications():
    data, errors = notifications_filter_schema.load(request.args)
    if errors:
        return jsonify(result="error", message=errors), 400

    page = data['page'] if 'page' in data else 1

    pagination = notifications_dao.get_notifications_for_service(
        api_user['client'],
        filter_dict=data,
        page=page)
    return jsonify(
        notifications=notification_status_schema.dump(pagination.items, many=True).data,
        links=pagination_links(
            pagination,
            '.get_all_notifications',
            **request.args.to_dict()
        )
    ), 200


@notifications.route('/service/<service_id>/notifications', methods=['GET'])
@require_admin()
def get_all_notifications_for_service(service_id):
    data, errors = notifications_filter_schema.load(request.args)
    if errors:
        return jsonify(result="error", message=errors), 400

    page = data['page'] if 'page' in data else 1

    pagination = notifications_dao.get_notifications_for_service(
        service_id,
        filter_dict=data,
        page=page)
    kwargs = request.args.to_dict()
    kwargs['service_id'] = service_id
    return jsonify(
        notifications=notification_status_schema.dump(pagination.items, many=True).data,
        links=pagination_links(
            pagination,
            '.get_all_notifications_for_service',
            **kwargs
        )
    ), 200


@notifications.route('/service/<service_id>/job/<job_id>/notifications', methods=['GET'])
@require_admin()
def get_all_notifications_for_service_job(service_id, job_id):
    data, errors = notifications_filter_schema.load(request.args)
    if errors:
        return jsonify(result="error", message=errors), 400

    page = data['page'] if 'page' in data else 1

    pagination = notifications_dao.get_notifications_for_job(
        service_id,
        job_id,
        filter_dict=data,
        page=page)
    kwargs = request.args.to_dict()
    kwargs['service_id'] = service_id
    kwargs['job_id'] = job_id
    return jsonify(
        notifications=notification_status_schema.dump(pagination.items, many=True).data,
        links=pagination_links(
            pagination,
            '.get_all_notifications_for_service_job',
            **kwargs
        )
    ), 200


def pagination_links(pagination, endpoint, **kwargs):
    if 'page' in kwargs:
        kwargs.pop('page', None)
    links = dict()
    if pagination.has_prev:
        links['prev'] = url_for(endpoint, page=pagination.prev_num, **kwargs)
    if pagination.has_next:
        links['next'] = url_for(endpoint, page=pagination.next_num, **kwargs)
        links['last'] = url_for(endpoint, page=pagination.pages, **kwargs)
    return links


@notifications.route('/notifications/<string:notification_type>', methods=['POST'])
def send_notification(notification_type):
    if notification_type not in ['sms', 'email']:
        assert False

    service_id = api_user['client']
    service = services_dao.dao_fetch_service_by_id(api_user['client'])

    service_stats = notifications_dao.dao_get_notification_statistics_for_service_and_day(
        service_id,
        datetime.utcnow().strftime(DATE_FORMAT)
    )

    if service_stats:
        total_sms_count = service_stats.sms_requested
        total_email_count = service_stats.emails_requested

        if (total_email_count + total_sms_count >= service.limit) and service.restricted:
            return jsonify(result="error", message='Exceeded send limits ({}) for today'.format(service.limit)), 429

    notification, errors = (
        sms_template_notification_schema if notification_type == 'sms' else email_notification_schema
    ).load(request.get_json())

    if errors:
        return jsonify(result="error", message=errors), 400

    template = templates_dao.dao_get_template_by_id_and_service_id(
        template_id=notification['template'],
        service_id=service_id
    )

    template_object = Template(template.__dict__, notification.get('personalisation', {}))
    if template_object.missing_data:
        return jsonify(
            result="error",
            message={
                'template': ['Missing personalisation: {}'.format(
                    ", ".join(template_object.missing_data)
                )]
            }
        ), 400
    if template_object.additional_data:
        return jsonify(
            result="error",
            message={
                'template': ['Personalisation not needed for template: {}'.format(
                    ", ".join(template_object.additional_data)
                )]
            }
        ), 400

    notification_id = create_uuid()

    if notification_type == 'sms':
        if not allowed_send_to_number(service, notification['to']):
            return jsonify(
                result="error", message={'to': ['Invalid phone number for restricted service']}), 400
        send_sms.apply_async((
            service_id,
            notification_id,
            encryption.encrypt(notification),
            datetime.utcnow().strftime(DATETIME_FORMAT)
        ), queue='sms')
    else:
        if not allowed_send_to_email(service, notification['to']):
            return jsonify(
                result="error", message={'to': ['Email address not permitted for restricted service']}), 400
        send_email.apply_async((
            service_id,
            notification_id,
            template.subject,
            "{}@{}".format(service.email_from, current_app.config['NOTIFY_EMAIL_DOMAIN']),
            encryption.encrypt(notification),
            datetime.utcnow().strftime(DATETIME_FORMAT)
        ), queue='email')

    return jsonify(data={"notification": {"id": notification_id}}), 201

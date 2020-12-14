from flask import json
from tests import create_authorization_header


def test_valid_post_broadcast_returns_201(
    client,
    sample_broadcast_service,
):
    auth_header = create_authorization_header(service_id=sample_broadcast_service.id)

    response = client.post(
        path='/v2/broadcast',
        data=json.dumps({}),
        headers=[('Content-Type', 'application/json'), auth_header],
    )

    assert response.status_code == 201

    response_json = json.loads(response.get_data(as_text=True))

    assert response_json['foo']

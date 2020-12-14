from flask import jsonify
from app import authenticated_service
from app.dao.dao_utils import dao_save_object
from app.notifications.validators import check_service_has_permission
from app.models import BROADCAST_TYPE, BroadcastMessage, BroadcastStatusType
from app.v2.broadcast import v2_broadcast_blueprint


@v2_broadcast_blueprint.route("", methods=['POST'])
def create_broadcast():

    check_service_has_permission(
        BROADCAST_TYPE,
        authenticated_service.permissions,
    )

    broadcast_message = BroadcastMessage(
        service_id=authenticated_service.id,
        template_id=None,
        template_version=None,
        personalisation={},
        areas={
            "areas": [],
            "simple_polygons": [],
        },
        status=BroadcastStatusType.PENDING_APPROVAL,
        starts_at=None,
        finishes_at=None,
        created_by_id=None,
    )

    dao_save_object(broadcast_message)

    return jsonify(broadcast_message.serialize()), 201

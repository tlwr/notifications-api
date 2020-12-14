from bs4 import BeautifulSoup


def cap_xml_to_dict(cap_xml):
    # This function assumes that it’s being passed valid CAP XML
    alert = BeautifulSoup(cap_xml)
    return {
        "reference": alert.identifier,
        "category": alert.info.category,
        "expires": alert.info.expires,
        "content": alert.info.description,
        "url": alert.info.web,
        "areas": [
            {
                "area_name": area.areaDesc,
                "polygons": [
                    cap_xml_polygon_to_list(polygon)
                    for polygon in area.find_all('polygon')
                ]
            }
            for area in alert.info.find_all('area')
        ]
    }


def cap_xml_polygon_to_list(polygon_string):
    return [
        [
            [
                float(x), float(y)
            ]
            for x, y in pair.split(',')
        ]
        for pair in polygon_string.split(' ')
    ]

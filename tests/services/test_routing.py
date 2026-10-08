from services import format_route, routing


def test_format_route():
    assert format_route.route(3500, "story", 1)["publish_method"] == "export"
    assert format_route.route(900, "story", 1)["target"] == "story900"
    assert format_route.route(300, "news", 0)["target"] == "news900"


def test_routing():
    assert routing.route("某男团首周销量105万张，厂牌战略分析") == "gzh"
    assert routing.route("某偶像私服街拍同框花絮") == "xhs"

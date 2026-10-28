"""Native Grafana presentation; measurements and selector scope stay intact."""
from copy import deepcopy

MUTED = "#A8B5C5"
BASE_IDS = {1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
            20, 21, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45}
HEADER_TITLE = "FortiLens / Network operations"


def item(pid, x, y, width, height):
    return {"kind": "GridLayoutItem", "spec": {"x": x, "y": y, "width": width, "height": height,
            "element": {"kind": "ElementReference", "name": "panel-" + str(pid)}}}


def row(title, items, *, collapsed=False, repeat=False, hidden=False):
    spec = {"title": title, "collapse": collapsed, "hideHeader": hidden, "fillScreen": False,
            "layout": {"kind": "GridLayout", "spec": {"items": items}}}
    if repeat:
        spec["repeat"] = {"mode": "variable", "value": "wan"}
    return {"kind": "RowsLayoutRow", "spec": spec}


def override(name, **properties):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in properties.items()]}


def polish_dashboard(source):
    result = deepcopy(source)
    spec, elements = result["spec"], result["spec"]["elements"]
    expected = {"panel-" + str(i) for i in BASE_IDS}
    if set(elements) - expected - {"panel-46"} or expected - set(elements):
        raise ValueError("Review custom/missing panels before applying the standard dashboard layout")
    if "panel-46" in elements and elements["panel-46"]["spec"].get("title") != HEADER_TITLE:
        raise ValueError("Panel ID46 is already used; preserve the existing panel")

    def panel(pid):
        return elements["panel-" + str(pid)]["spec"]

    def fields(pid):
        return panel(pid)["vizConfig"]["spec"]["fieldConfig"]

    def stat(pid, *, value_size=32, names=False):
        p = panel(pid)
        p["vizConfig"]["group"] = "stat"
        p["vizConfig"]["spec"]["options"] = {
            "colorMode": "value", "graphMode": "none", "justifyMode": "center",
            "orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showPercentChange": False, "textMode": "value_and_name" if names else "value",
            "wideLayout": True, "text": {"titleSize": 13, "valueSize": value_size}}
        fields(pid)["defaults"]["noValue"] = "NO DATA"

    for pid in (9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21):
        stat(pid, value_size=26 if pid in (9, 14, 18) else 32)
    for pid in (38, 39, 40):
        stat(pid, value_size=28, names=True)
    stat(3, value_size=28, names=True)
    for pid in (10, 11, 15, 16, 17):
        fields(pid)["defaults"].update(decimals=1, color={"mode": "fixed", "fixedColor": "blue"})
    for pid in (19, 20):
        fields(pid)["defaults"].update(decimals=1, color={"mode": "fixed", "fixedColor": "blue"})
    fields(21)["defaults"]["decimals"] = 2
    # A scrape failure is a collection result, not proof of a dead firewall/WAN.
    panel(9).update(title="FortiGate collection", description="Exporter scrape result for the selected target. A failed scrape does not prove the firewall or WAN is down.")
    fields(9)["defaults"].update(decimals=0, color={"mode": "thresholds"}, mappings=[
        {"type": "value", "options": {"0": {"text": "SCRAPE FAILED", "color": "orange"},
                                         "1": {"text": "COLLECTING", "color": "green"}}},
        {"type": "special", "options": {"match": "null", "result": {"text": "NO DATA", "color": MUTED}}}],
        thresholds={"mode": "absolute", "steps": [{"value": 0, "color": "orange"}, {"value": 1, "color": "green"}]})
    panel(14)["title"] = "Monitoring host"
    panel(10)["title"] = "Firewall CPU"
    panel(11)["title"] = "Firewall memory"
    panel(12)["title"] = "Active sessions"
    panel(15)["title"] = "Disk used"
    panel(16)["title"] = "Host CPU"
    panel(17)["title"] = "Host memory"
    for pid in (3,):
        fields(pid)["defaults"]["color"] = {"mode": "fixed", "fixedColor": "blue"}
        for rule in fields(pid)["overrides"]:
            if rule["matcher"]["options"] in ("Download", "Upload"):
                rule["properties"] = [p for p in rule["properties"] if p["id"] not in {"max", "decimals", "color"}]
                rule["properties"].extend([{"id": "decimals", "value": 1},
                    {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue" if rule["matcher"]["options"] == "Download" else "purple"}}])

    for pid in (1, 2, 41):
        p = panel(pid)
        p["title"] = {1: "Recorded device usage / Today", 41: "Recorded device usage / Last 7 days", 2: "Recorded device usage / This month"}[pid]
        config = fields(pid)
        config["defaults"].update(unit="none", decimals=2, noValue="NO DATA",
            color={"mode": "fixed", "fixedColor": "blue"},
            thresholds={"mode": "absolute", "steps": [{"value": 0, "color": "blue"}]})
        config["overrides"] = [override("Name", custom__minWidth=180),
            override("IP Address", custom__width=160), override("MAC Address", custom__width=180),
            override("Internet Usage", displayName="Total (GB)", custom__width=180, custom__align="right",
                     custom__cellOptions={"type": "gauge", "mode": "basic", "valueDisplayMode": "text"})]
        p["vizConfig"]["spec"]["options"] = {"cellHeight": "md", "showHeader": True,
            "sortBy": [{"desc": True, "displayName": "Total (GB)"}]}

    for pid in (7, 8):
        p = panel(pid)
        p["title"] = "Interface traffic / " + ("Today" if pid == 7 else "This month")
        p["description"] = "Selected-interface RX + TX counter estimate in decimal GB. Counter increases extrapolate at time-window edges; missing collection is not zero traffic."
        p["vizConfig"]["spec"]["options"] = {"cellHeight": "md", "showHeader": True,
            "sortBy": [{"desc": True, "displayName": "Total (GB)"}]}
        config = fields(pid)
        config["defaults"].update(unit="none", decimals=2, noValue="NO DATA")
        config["overrides"] = [override("WAN", displayName="Interface", custom__width=180),
            override("Usage (GB)", displayName="Total (GB)", custom__align="right",
                     custom__cellOptions={"type": "gauge", "mode": "basic", "valueDisplayMode": "text"})]
    for pid in (43, 44, 45):
        config = fields(pid)
        config["defaults"].update(unit="none", decimals=0, noValue="NO DATA")
        config["overrides"] = [override("Received records", custom__width=180, custom__align="right")]
    for pid in (13, 35, 36, 37):
        p = panel(pid)
        opts = p["vizConfig"]["spec"]["options"]
        opts["legend"] = {"calcs": ["lastNotNull"], "displayMode": "table", "placement": "bottom", "showLegend": True}
        opts["tooltip"] = {"hideZeros": False, "mode": "multi", "sort": "desc"}
        defaults = fields(pid)["defaults"]
        defaults["decimals"] = 1 if pid != 37 else 2
        defaults["custom"].update(lineWidth=2, fillOpacity=8, showPoints="never", spanNulls=False,
                                  axisBorderShow=False, axisColorMode="text")
    panel(13)["title"] = "Bandwidth / Download & upload"

    elements["panel-46"] = {"kind": "Panel", "spec": {"id": 46, "title": HEADER_TITLE, "description": "Dashboard navigation and scope at a glance.", "links": [],
        "data": {"kind": "QueryGroup", "spec": {"queries": [], "transformations": [], "queryOptions": {}}},
        "vizConfig": {"kind": "VizConfig", "group": "text", "version": "13.2.2", "spec": {
            "options": {"mode": "markdown", "content": "Monitor the selected firewall and interfaces. **NO DATA** means telemetry is unavailable; it does not mean zero usage.\n\n**Scope:** WAN selectors apply to performance and interface traffic. Device reports use the selected VDOM. Expand the sections below for weekly reports, history and data-quality evidence."},
            "fieldConfig": {"defaults": {}, "overrides": []}}}}}
    spec["layout"] = {"kind": "RowsLayout", "spec": {"rows": [
        row("Start here", [item(46, 0, 0, 24, 4)], hidden=True),
        row("Overview", [item(9, 0, 0, 6, 4), item(14, 6, 0, 6, 4), item(10, 12, 0, 4, 4), item(11, 16, 0, 4, 4), item(12, 20, 0, 4, 4)]),
        row("WAN quality / ${wan}", [item(18, 0, 0, 6, 4), item(19, 6, 0, 6, 4), item(20, 12, 0, 6, 4), item(21, 18, 0, 6, 4)], repeat=True),
        row("Traffic overview", [item(13, 0, 0, 24, 8), item(7, 0, 8, 12, 7), item(8, 12, 8, 12, 7)]),
        row("Device usage / Today", [item(1, 0, 0, 24, 10)]),
        row("Device usage / Last 7 days", [item(41, 0, 0, 24, 12)], collapsed=True),
        row("Device usage / This month", [item(2, 0, 0, 24, 12)], collapsed=True),
        row("Availability & sample coverage", [item(38, 0, 0, 8, 6), item(39, 8, 0, 8, 6), item(40, 16, 0, 8, 6)], collapsed=True),
        row("Performance history / Last 30 days", [item(34, 0, 0, 24, 6), item(35, 0, 6, 12, 8), item(36, 12, 6, 12, 8), item(37, 0, 14, 24, 7)], collapsed=True),
        row("Physical interface / ${wan}", [item(3, 0, 0, 24, 5)], collapsed=True, repeat=True),
        row("Monitoring host / Resources", [item(15, 0, 0, 8, 4), item(16, 8, 0, 8, 4), item(17, 16, 0, 8, 4)], collapsed=True),
        row("Usage Data Quality / Received records", [item(43, 0, 0, 24, 8), item(44, 0, 8, 24, 8), item(45, 0, 16, 24, 8)], collapsed=True),
        row("Help / Reporting periods & evidence", [item(42, 0, 0, 24, 9)], collapsed=True),
    ]}}
    return result

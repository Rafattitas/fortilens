"""Native Grafana presentation; measurements and selector scope stay intact."""
from copy import deepcopy

MUTED = "#A8B5C5"
BASE_IDS = {3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
            20, 21, 34, 35, 36, 37, 38, 39, 40}
# Former device-usage tables (1, 41, 2), their help text (42) and received-record
# counts (43-45). Each scanned raw Loki logs for its whole period on every view,
# which outran the console gateway; the console's Reports and Data quality pages
# show the same evidence from the accounting store.
RETIRED_IDS = {1, 2, 41, 42, 43, 44, 45}
HEADER_TITLE = "FortiLens / Network operations"
HEADER_LEAD = ("Monitor the selected firewall and interfaces. **NO DATA** means telemetry is unavailable; "
               "it does not mean zero usage.\n\n**Scope:** WAN selectors apply to performance and interface traffic. ")
HEADER_CONTENT = HEADER_LEAD + ("Expand the sections below for availability, history and host resources. "
    "Recorded device usage is in the FortiLens console under Reports, and received-log evidence under Data quality.")
# The header up to v0.5.0 pointed at the retired sections.
PREVIOUS_HEADER_CONTENT = HEADER_LEAD + ("Device reports use the selected VDOM. "
    "Expand the sections below for weekly reports, history and data-quality evidence.")


def panel_id(name):
    prefix, _, number = name.partition("-")
    return int(number) if prefix == "panel" and number.isdigit() else None


def is_retired_panel(name, element):
    """A retired panel exactly as FortiLens generated it: ID, title and content.

    Anything else with one of those IDs is treated as an operator's panel."""
    pid = panel_id(name)
    spec = element.get("spec") if isinstance(element, dict) else None
    if pid not in RETIRED_IDS or not isinstance(spec, dict) or spec.get("id") != pid:
        return False
    title = spec.get("title") if isinstance(spec.get("title"), str) else ""
    data = spec.get("data") if isinstance(spec.get("data"), dict) else {}
    queries = (data.get("spec") or {}).get("queries") or []
    if pid == 42:
        return title == "Reporting Periods & Evidence" and not queries
    found = [((q.get("spec") or {}).get("query") or {}) for q in queries if isinstance(q, dict)]
    exprs = [(query.get("spec") or {}).get("expr") for query in found]
    if not found or len(found) != len(queries) or any(query.get("group") != "loki" for query in found) or not all(
            isinstance(expr, str) and '{job="fortigate-syslog"}' in expr for expr in exprs):
        return False
    if pid in (43, 44, 45):
        return title.startswith("Received Usage Records") and all("usage_quality" in expr for expr in exprs)
    return title.startswith(("Recorded Internet Usage by Device", "Recorded device usage")) and all(
        "mastersrcmac" in expr for expr in exprs)


def references(value):
    """Element names a layout subtree places on the dashboard."""
    if isinstance(value, list):
        return [name for child in value for name in references(child)]
    if not isinstance(value, dict):
        return []
    if value.get("kind") == "ElementReference" and isinstance(value.get("name"), str):
        return [value["name"]]
    return [name for child in value.values() for name in references(child)]


def drop_references(layout, names):
    """Remove layout items for the given elements, and any row or tab left with none."""
    if isinstance(layout, list):
        for child in layout:
            drop_references(child, names)
        return
    if not isinstance(layout, dict):
        return
    spec = layout.get("spec")
    if not isinstance(spec, dict):
        return
    if isinstance(spec.get("items"), list):
        spec["items"] = [i for i in spec["items"] if not set(references(i)) & names]
    for key in ("rows", "tabs"):
        if isinstance(spec.get(key), list):
            kept = []
            for child in spec[key]:
                emptied = bool(set(references(child)) & names)
                drop_references(child, names)
                if not (emptied and not references(child)):
                    kept.append(child)
            spec[key] = kept
    drop_references(spec.get("layout"), names)


def retire_usage_panels(source):
    """Remove the retired panels and their emptied rows; leave everything else as it is.

    Returns the dashboard, the removed panel names and any retired IDs that were
    left because their content no longer matches what FortiLens generated."""
    result = deepcopy(source)
    spec = result["spec"]
    elements = spec["elements"]
    removed = sorted(name for name, element in elements.items() if is_retired_panel(name, element))
    kept = sorted(name for name in elements if name not in removed and panel_id(name) in RETIRED_IDS)
    for name in removed:
        del elements[name]
    drop_references(spec.get("layout"), set(removed))
    # Update the generated header only if it is still word for word what FortiLens wrote.
    header = ((elements.get("panel-46") or {}).get("spec") or {})
    options = (((header.get("vizConfig") or {}).get("spec") or {}).get("options") or {})
    if removed and header.get("title") == HEADER_TITLE and options.get("content") == PREVIOUS_HEADER_CONTENT:
        options["content"] = HEADER_CONTENT
    return result, removed, kept


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
    # A saved dashboard from an older release still carries the retired panels.
    result = retire_usage_panels(source)[0]
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
            "options": {"mode": "markdown", "content": HEADER_CONTENT},
            "fieldConfig": {"defaults": {}, "overrides": []}}}}}
    spec["layout"] = {"kind": "RowsLayout", "spec": {"rows": [
        row("Start here", [item(46, 0, 0, 24, 4)], hidden=True),
        row("Overview", [item(9, 0, 0, 6, 4), item(14, 6, 0, 6, 4), item(10, 12, 0, 4, 4), item(11, 16, 0, 4, 4), item(12, 20, 0, 4, 4)]),
        row("WAN quality / ${wan}", [item(18, 0, 0, 6, 4), item(19, 6, 0, 6, 4), item(20, 12, 0, 6, 4), item(21, 18, 0, 6, 4)], repeat=True),
        row("Traffic overview", [item(13, 0, 0, 24, 8), item(7, 0, 8, 12, 7), item(8, 12, 8, 12, 7)]),
        row("Availability & sample coverage", [item(38, 0, 0, 8, 6), item(39, 8, 0, 8, 6), item(40, 16, 0, 8, 6)], collapsed=True),
        row("Performance history / Last 30 days", [item(34, 0, 0, 24, 6), item(35, 0, 6, 12, 8), item(36, 12, 6, 12, 8), item(37, 0, 14, 24, 7)], collapsed=True),
        row("Physical interface / ${wan}", [item(3, 0, 0, 24, 5)], collapsed=True, repeat=True),
        row("Monitoring host / Resources", [item(15, 0, 0, 8, 4), item(16, 8, 0, 8, 4), item(17, 16, 0, 8, 4)], collapsed=True),
    ]}}
    return result

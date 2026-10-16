"""Immutable original binder diagnostic; never use for current issuer attribution."""
from market_checker_app.services.sec13f_identity_bulk import _TickerCoverParser


class _IssuerNameParser(_TickerCoverParser):
    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        if tag == "ix:nonnumeric" and str(attributes.get("name", "")).casefold() == "dei:entityregistrantname":
            if (self.namespaces != {"dei", "ix", "xbrli"} or self.context
                    or not attributes.get("contextref") or attributes.get("continuedat")
                    or str(attributes.get("xsi:nil", "")).casefold() in {"true", "1"}
                    or any(capture[0] == tag for capture in self.captures)):
                raise ValueError("Incomplete independent issuer-name fact")
            self.captures.append([tag, attributes["contextref"], "dei:entityregistrantname", []])


def issuer_names(snapshot):
    parser = _IssuerNameParser()
    parser.feed(snapshot.payload.decode("utf-8-sig"))
    parser.close()
    if parser.captures or parser.context is not None or parser.namespaces != {"dei", "ix", "xbrli"}:
        raise ValueError("Incomplete independent issuer-name filing")
    result = []
    for context, values in parser.facts.items():
        names = values.get("dei:entityregistrantname", [])
        identity = parser.contexts.get(context, {})
        ciks = identity.get("xbrli:identifier", [])
        anchors = identity.get("xbrli:instant", []) + identity.get("xbrli:enddate", [])
        if len(names) == len(ciks) == len(anchors) == 1 and 1 <= len(names[0]) <= 300:
            result.append({"issuer_name": names[0], "issuer_cik": ciks[0].zfill(10),
                           "anchor_date": anchors[0], "context_ref": context})
    return result

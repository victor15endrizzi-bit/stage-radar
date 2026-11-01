from .base import Connector
from .cornerstone import CornerstoneConnector
from .sitemap import SitemapConnector
from .successfactors import SuccessFactorsConnector
from .email_alerts import EmailAlertsConnector
from .links import LinksConnector
from .moovijob import MoovijobConnector
from .oracle_hcm import OracleHCMConnector
from .smartrecruiters import SmartRecruitersConnector
from .workday import WorkdayConnector

REGISTRY = {
    "moovijob": MoovijobConnector,
    "workday": WorkdayConnector,
    "oracle": OracleHCMConnector,
    "smartrecruiters": SmartRecruitersConnector,
    "cornerstone": CornerstoneConnector,
    "successfactors": SuccessFactorsConnector,
    "sitemap": SitemapConnector,
    "links": LinksConnector,
    "email": EmailAlertsConnector,
}


def make_connector(source: dict, session=None) -> Connector:
    kind = source["kind"]
    if kind not in REGISTRY:
        raise ValueError(f"Type de source inconnu : {kind}")
    return REGISTRY[kind](source, session=session)

from __future__ import annotations

import html
from pathlib import Path
from string import Template

TEMPLATE = Template((Path(__file__).parents[1] / "web" / "oauth.html").read_text())


class AuthorizationPage:
    def render(self, application: str, title: str, body: str) -> str:
        return TEMPLATE.substitute(
            application=html.escape(application), title=html.escape(title), body=body
        )

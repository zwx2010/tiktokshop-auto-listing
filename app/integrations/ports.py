from __future__ import annotations

from typing import Protocol


class CopyGenerator(Protocol):
    def generate(self, product: dict, market_code: str) -> dict: ...


class ImageQaGateway(Protocol):
    def check(self, image_urls: list[str], market_code: str) -> dict: ...


class FeishuGateway(Protocol):
    def send_approval(self, payload: dict) -> str: ...
    def parse_callback(self, payload: dict) -> dict: ...


class CdpGateway(Protocol):
    def submit_listing(self, payload: dict) -> dict: ...

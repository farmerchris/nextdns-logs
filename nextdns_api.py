#!/usr/bin/env python3
"""Shared NextDNS API client helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests

from nextdns_common import resolve_api_key

API_BASE = "https://api.nextdns.io"


@dataclass
class NextDNSAPIError(RuntimeError):
    message: str
    status_code: Optional[int] = None
    errors: Optional[List[Dict[str, Any]]] = None
    response_text: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

    def __str__(self) -> str:
        return self.message


class NextDNSClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_BASE,
        timeout: int = 30,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._owns_session = session is None

    @classmethod
    def from_cli_api_key(cls, cli_api_key: Optional[str]) -> "NextDNSClient":
        return cls(resolve_api_key(cli_api_key))

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> "NextDNSClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        stream: bool = False,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        resp = self._session.request(
            method,
            url,
            headers={"X-Api-Key": self.api_key},
            params=params,
            json=json_body,
            timeout=timeout or self.timeout,
            stream=stream,
        )

        if resp.status_code != 200:
            text = ""
            errors: Optional[List[Dict[str, Any]]] = None
            if not stream:
                text = resp.text.strip()
                try:
                    payload = resp.json()
                    if isinstance(payload, dict) and isinstance(
                        payload.get("errors"), list
                    ):
                        errors = payload["errors"]
                except ValueError:
                    pass
            raise NextDNSAPIError(
                message=f"HTTP {resp.status_code} from NextDNS API: {text or resp.reason}",
                status_code=resp.status_code,
                errors=errors,
                response_text=text or None,
                headers=dict(resp.headers),
            )

        return resp

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        resp = self._request(
            method,
            path,
            params=params,
            json_body=json_body,
            timeout=timeout,
            stream=False,
        )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise NextDNSAPIError(
                message=f"Invalid JSON response from NextDNS API for {method} {path}",
                status_code=resp.status_code,
                response_text=resp.text.strip(),
                headers=dict(resp.headers),
            ) from exc

        if (
            isinstance(payload, dict)
            and isinstance(payload.get("errors"), list)
            and payload["errors"]
        ):
            raise NextDNSAPIError(
                message=f"NextDNS API returned errors for {method} {path}: {payload['errors']}",
                status_code=resp.status_code,
                errors=payload["errors"],
                response_text=resp.text.strip(),
                headers=dict(resp.headers),
            )

        return payload

    def get_stream(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 90,
    ) -> requests.Response:
        return self._request("GET", path, params=params, timeout=timeout, stream=True)

    def list_profiles(self) -> List[Dict[str, Any]]:
        payload = self.request_json("GET", "/profiles")
        return payload.get("data", [])

    def get_paginated(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        base_params: Dict[str, Any] = dict(params or {})

        while True:
            request_params = dict(base_params)
            if cursor:
                request_params["cursor"] = cursor

            payload = self.request_json("GET", path, params=request_params)
            rows.extend(payload.get("data", []))
            cursor = payload.get("meta", {}).get("pagination", {}).get("cursor")
            if not cursor:
                break

        return rows

    def analytics_domains(
        self,
        *,
        profile_id: str,
        from_time: str,
        to_time: str,
        status: str,
        limit: int = 500,
        root: bool = False,
        progress: Optional[Callable[[str, bool], None]] = None,
    ) -> List[Dict[str, Any]]:
        if not (1 <= limit <= 500):
            raise ValueError("--limit must be between 1 and 500")

        cursor: Optional[str] = None
        page = 0
        rows: List[Dict[str, Any]] = []

        if progress:
            progress(f"querying {from_time} -> {to_time}", done=False)

        while True:
            page += 1
            params: Dict[str, Any] = {
                "status": status,
                "from": from_time,
                "to": to_time,
                "limit": limit,
            }
            if root:
                params["root"] = "1"
            if cursor:
                params["cursor"] = cursor

            payload = self.request_json(
                "GET", f"/profiles/{profile_id}/analytics/domains", params=params
            )
            data = payload.get("data", [])
            rows.extend(data)

            if progress:
                progress(
                    f"page {page}, got {len(data)} rows, total {len(rows)}",
                    done=False,
                )

            cursor = payload.get("meta", {}).get("pagination", {}).get("cursor")
            if not cursor:
                break

        rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
        if progress:
            progress(f"done ({len(rows)} rows)", done=True)
        return rows

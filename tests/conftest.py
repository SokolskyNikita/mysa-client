"""Standalone fixtures shared by cloud and HomeKit protocol tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import aiohttp
import pytest

from tests.cloud_helpers import FakeCloud, StandaloneAiohttpMocker


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """An ordinary aiohttp session owned by the test."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


@pytest.fixture
def aioclient_mock() -> Iterator[StandaloneAiohttpMocker]:
    """Intercept aiohttp requests without Home Assistant test helpers."""
    with StandaloneAiohttpMocker() as mock:
        yield mock


@pytest.fixture
def cloud(aioclient_mock: StandaloneAiohttpMocker) -> FakeCloud:
    """A small local simulation of Mysa's cloud and Cognito endpoints."""
    return FakeCloud(aioclient_mock)

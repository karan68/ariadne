import pytest

from app.cognee_client import MockCogneeClient, get_client


@pytest.fixture
def mock_client():
    return MockCogneeClient()


async def test_get_client_returns_mock_when_requested():
    assert isinstance(get_client(mock=True), MockCogneeClient)


async def test_mock_remember_records_call(mock_client):
    res = await mock_client.remember("some text", dataset_name="patient_x_clinical")
    assert res["dataset"] == "patient_x_clinical"
    assert ("remember", "patient_x_clinical") in mock_client.calls


async def test_mock_recall_returns_empty_by_default(mock_client):
    res = await mock_client.recall("Where is Doug?", query_type="TEMPORAL")
    assert res == []
    assert mock_client.calls[-1][0] == "recall"


async def test_mock_forget_reports_forgotten(mock_client):
    res = await mock_client.forget("d123", "patient_x_clinical")
    assert res["forgotten"] == "d123"

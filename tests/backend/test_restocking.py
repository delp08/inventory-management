"""
Tests for restocking order API endpoints.
"""
import pytest

import main


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """Restock orders live in module-level memory, so reset them around each test."""
    main.restock_orders.clear()
    yield
    main.restock_orders.clear()


def get_forecast(client, sku):
    """Fetch a single demand forecast record by SKU."""
    forecasts = client.get("/api/demand").json()
    return next(f for f in forecasts if f["item_sku"] == sku)


class TestRestockingOrderEndpoints:
    """Test suite for restocking order endpoints."""

    def test_demand_forecasts_include_cost_and_category(self, client):
        """Test that demand forecasts carry the fields restocking needs."""
        data = client.get("/api/demand").json()
        assert len(data) > 0

        for forecast in data:
            assert isinstance(forecast["unit_cost"], (int, float))
            assert forecast["unit_cost"] > 0
            assert forecast["category"]

    def test_create_restock_order(self, client):
        """Test submitting a valid restocking order."""
        forecast = get_forecast(client, "WDG-001")
        response = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [{"sku": "WDG-001", "quantity": 10}]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["status"] == "Submitted"
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "WDG-001"
        assert order["items"][0]["unit_cost"] == forecast["unit_cost"]
        assert abs(order["total_value"] - 10 * forecast["unit_cost"]) < 0.01

    def test_restock_order_lead_time_is_longest_item(self, client):
        """Test that lead time is the longest among the ordered items."""
        # Actuators (10 days) and Sensors (5 days)
        response = client.post("/api/restocking-orders", json={
            "budget": 100000,
            "items": [
                {"sku": "WDG-001", "quantity": 1},
                {"sku": "SNR-420", "quantity": 1}
            ]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["lead_time_days"] == main.LEAD_TIME_DAYS["Actuators"]
        assert "T" in order["expected_delivery"]
        assert order["expected_delivery"] > order["order_date"]

    def test_restock_order_price_comes_from_server(self, client):
        """Test that the total is computed server-side from forecast data."""
        forecast = get_forecast(client, "SNR-420")
        response = client.post("/api/restocking-orders", json={
            "budget": 100000,
            "items": [{"sku": "SNR-420", "quantity": 4, "unit_cost": 0.01}]
        })
        assert response.status_code == 201
        assert abs(response.json()["total_value"] - 4 * forecast["unit_cost"]) < 0.01

    def test_restock_order_unknown_sku(self, client):
        """Test that an unknown SKU returns 404."""
        response = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [{"sku": "NOPE-999", "quantity": 1}]
        })
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_restock_order_invalid_quantity(self, client):
        """Test that a non-positive quantity returns 400."""
        response = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [{"sku": "WDG-001", "quantity": 0}]
        })
        assert response.status_code == 400

    def test_restock_order_empty_items(self, client):
        """Test that an order without items returns 400."""
        response = client.post("/api/restocking-orders", json={"budget": 10000, "items": []})
        assert response.status_code == 400

    def test_restock_order_over_budget(self, client):
        """Test that an order exceeding the budget is rejected."""
        response = client.post("/api/restocking-orders", json={
            "budget": 1,
            "items": [{"sku": "MTR-304", "quantity": 5}]
        })
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restocking-orders").json() == []

    def test_restock_order_invalid_body(self, client):
        """Test that a malformed body returns a validation error."""
        response = client.post("/api/restocking-orders", json={"items": []})
        assert response.status_code == 422

    def test_restock_order_rejects_already_ordered_sku(self, client):
        """Test that a SKU on a submitted order cannot be ordered again."""
        payload = {"budget": 10000, "items": [{"sku": "WDG-001", "quantity": 1}]}
        assert client.post("/api/restocking-orders", json=payload).status_code == 201

        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 409
        assert "WDG-001" in response.json()["detail"]
        assert len(client.get("/api/restocking-orders").json()) == 1

    def test_restock_order_rejects_duplicate_skus_in_request(self, client):
        """Test that the same SKU twice in one request is rejected."""
        response = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [{"sku": "WDG-001", "quantity": 1}, {"sku": "WDG-001", "quantity": 2}]
        })
        assert response.status_code == 400

    def test_delete_restock_order(self, client):
        """Test deleting an order removes it from the list."""
        order = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [{"sku": "WDG-001", "quantity": 1}]
        }).json()

        response = client.delete(f"/api/restocking-orders/{order['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == order["id"]
        assert client.get("/api/restocking-orders").json() == []

    def test_delete_restock_order_allows_reordering(self, client):
        """Test that deleting an order frees its SKUs to be ordered again."""
        payload = {"budget": 10000, "items": [{"sku": "WDG-001", "quantity": 1}]}
        order = client.post("/api/restocking-orders", json=payload).json()
        client.delete(f"/api/restocking-orders/{order['id']}")

        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 201

    def test_delete_nonexistent_restock_order(self, client):
        """Test deleting an order that doesn't exist returns 404."""
        response = client.delete("/api/restocking-orders/RST-999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_restock_order_ids_not_reused_after_delete(self, client):
        """Test that ids stay unique after deleting the newest order."""
        first = client.post("/api/restocking-orders", json={
            "budget": 10000, "items": [{"sku": "WDG-001", "quantity": 1}]
        }).json()
        second = client.post("/api/restocking-orders", json={
            "budget": 10000, "items": [{"sku": "SNR-420", "quantity": 1}]
        }).json()
        client.delete(f"/api/restocking-orders/{second['id']}")
        third = client.post("/api/restocking-orders", json={
            "budget": 10000, "items": [{"sku": "SNR-420", "quantity": 1}]
        }).json()

        ids = {first["id"], second["id"], third["id"]}
        assert len(ids) == 3
        assert third["order_number"] != second["order_number"]

    def test_get_restock_orders_newest_first(self, client):
        """Test that submitted orders are listed newest first."""
        assert client.get("/api/restocking-orders").json() == []

        for sku in ["WDG-001", "SNR-420"]:
            client.post("/api/restocking-orders", json={
                "budget": 10000,
                "items": [{"sku": sku, "quantity": 1}]
            })

        data = client.get("/api/restocking-orders").json()
        assert len(data) == 2
        assert data[0]["items"][0]["sku"] == "SNR-420"
        assert data[1]["items"][0]["sku"] == "WDG-001"
        assert data[0]["order_number"] != data[1]["order_number"]

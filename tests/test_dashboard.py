from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from models import Order, OrderItem, Product, SessionLocal, User

PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_dashboard_smoke_with_one_order(seeded_database):
    with SessionLocal() as session:
        user = User(telegram_id=910001, username="dash_user", first_name="Мария")
        product = session.query(Product).order_by(Product.id).first()
        session.add(user)
        session.commit()

        order = Order(
            user_id=user.telegram_id,
            total_price=Decimal("1000.00"),
            status="new",
        )
        order.items.append(OrderItem(product_id=product.id, quantity=2))
        session.add(order)
        session.commit()

    st.cache_data.clear()
    app = AppTest.from_file(str(PROJECT_DIR / "dashboard.py"), default_timeout=30).run()

    assert not app.exception, [str(error) for error in app.exception]
    assert app.title[0].value.startswith("🍵")
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["💰 Общая выручка"] == "1 000 ₽"
    assert metrics["📦 Заказов"] == "1"
    assert metrics["🧾 Средний чек"] == "1 000 ₽"
    assert app.dataframe

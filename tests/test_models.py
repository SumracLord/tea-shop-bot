from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from models import (
    DEMO_PRODUCTS,
    Order,
    OrderItem,
    Product,
    SessionLocal,
    User,
    init_db,
    product_to_dict,
    utcnow,
)


def test_utcnow_is_timezone_aware():
    assert utcnow().tzinfo is timezone.utc


def test_demo_products_have_no_explicit_ids():
    # Явные id ломают sequence в PostgreSQL: INSERT с id не сдвигает
    # последовательность, и следующая обычная вставка получает конфликт.
    assert all("id" not in product for product in DEMO_PRODUCTS)


def test_init_db_seeds_five_products():
    init_db()
    with SessionLocal() as session:
        products = session.query(Product).order_by(Product.id).all()
        assert [product.id for product in products] == [1, 2, 3, 4, 5]
        assert products[0].name == "Шу Пуэр «Лун Чжу»"
        assert products[0].price == Decimal("500.00")


def test_init_db_is_idempotent():
    init_db()
    init_db()
    with SessionLocal() as session:
        assert session.query(Product).count() == 5


def test_new_product_after_seed_gets_new_id():
    init_db()
    with SessionLocal() as session:
        product = Product(name="Новый чай", price=Decimal("100.00"))
        session.add(product)
        session.commit()
        assert product.id == 6


def test_product_to_dict():
    product = Product(
        id=7,
        name="Тест",
        description=None,
        price=Decimal("123.45"),
        image_url=None,
    )
    assert product_to_dict(product) == {
        "id": 7,
        "name": "Тест",
        "description": "",
        "price": 123.45,
        "image_url": "",
    }


def test_order_items_cascade_on_order_delete(seeded_database):
    with SessionLocal() as session:
        product = session.query(Product).first()
        user = User(telegram_id=1001, username="buyer", first_name="Иван")
        session.add(user)
        session.commit()

        order = Order(
            user_id=user.telegram_id,
            total_price=Decimal("500.00"),
            status="new",
        )
        order.items.append(OrderItem(product_id=product.id, quantity=2))
        session.add(order)
        session.commit()
        order_id = order.id

        session.delete(order)
        session.commit()

        assert session.get(Order, order_id) is None
        assert session.query(OrderItem).count() == 0
        assert session.get(User, 1001) is not None


def test_user_delete_cascades_orders_and_items(seeded_database):
    with SessionLocal() as session:
        product = session.query(Product).first()
        user = User(telegram_id=1002, username="buyer2", first_name="Пётр")
        session.add(user)
        session.commit()

        order = Order(
            user_id=user.telegram_id,
            total_price=Decimal("650.00"),
            status="new",
        )
        order.items.append(OrderItem(product_id=product.id, quantity=1))
        session.add(order)
        session.commit()

        session.delete(user)
        session.commit()

        assert session.query(Order).count() == 0
        assert session.query(OrderItem).count() == 0

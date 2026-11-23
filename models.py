"""
models.py — модели SQLAlchemy и инициализация каталога.

Таблицы:
  * users       — покупатели Telegram;
  * products    — сорта чая;
  * orders      — заказы;
  * order_items — позиции заказов.

Файл не зависит от конкретной СУБД: одинаково работает с SQLite
(локальное демо) и PostgreSQL/Supabase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import relationship

from config import Base, SessionLocal, engine


def utcnow() -> datetime:
    """Текущее время в UTC для created_at/registered_at."""
    return datetime.now(timezone.utc)


class User(Base):
    """Покупатель Telegram. Первичный ключ — telegram_id, не автоинкремент."""

    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, autoincrement=False)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    registered_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User telegram_id={self.telegram_id} username={self.username!r}>"


class Product(Base):
    """Товар — сорт китайского чая."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    image_url = Column(String(512), nullable=True)

    def __repr__(self) -> str:
        return f"<Product id={self.id} name={self.name!r} price={self.price}>"


class Order(Base):
    """Заказ пользователя."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    total_price = Column(Numeric(10, 2), nullable=False)
    status = Column(String(32), nullable=False, default="new")
    created_at = Column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    user = relationship("User", back_populates="orders")
    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} user_id={self.user_id} "
            f"total={self.total_price} status={self.status!r}>"
        )


class OrderItem(Base):
    """Позиция заказа: товар и количество."""

    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id = Column(
        Integer,
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity = Column(Integer, nullable=False, default=1)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")

    def __repr__(self) -> str:
        return (
            f"<OrderItem id={self.id} order_id={self.order_id} "
            f"product_id={self.product_id} qty={self.quantity}>"
        )


DEMO_PRODUCTS = (
    {
        "name": "Шу Пуэр «Лун Чжу»",
        "description": "Выдержанный чёрный пуэр с орехово-землистым вкусом.",
        "price": Decimal("500.00"),
        "image_url": "https://cdn.pixabay.com/photo/2016/03/27/21/39/tea-1283606_640.jpg",
    },
    {
        "name": "Те Гуань Инь",
        "description": "Светлый улун с цветочным ароматом и сливочным послевкусием.",
        "price": Decimal("450.00"),
        "image_url": "https://cdn.pixabay.com/photo/2016/01/20/16/54/tea-1151656_640.jpg",
    },
    {
        "name": "Да Хун Пао",
        "description": "Тёмный скальный улун, медово-пряный и насыщенный.",
        "price": Decimal("650.00"),
        "image_url": "https://cdn.pixabay.com/photo/2017/08/07/14/34/tea-2602694_640.jpg",
    },
    {
        "name": "Лун Дзин",
        "description": "Знаменитый зелёный чай из Ханчжоу с нежным вкусом.",
        "price": Decimal("400.00"),
        "image_url": "https://cdn.pixabay.com/photo/2016/03/27/21/39/tea-1283606_640.jpg",
    },
    {
        "name": "Цзинь Цзюнь Мэй",
        "description": "Красный чай с золотыми почками, сладкий и медовый.",
        "price": Decimal("700.00"),
        "image_url": "https://cdn.pixabay.com/photo/2016/01/20/16/54/tea-1151656_640.jpg",
    },
)


def product_to_dict(product: Product) -> dict:
    """JSON-представление товара для витрины."""
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description or "",
        "price": float(product.price),
        "image_url": product.image_url or "",
    }


def init_db() -> None:
    """Создаёт таблицы (если их нет) и наполняет каталог демо-товарами."""
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()
    try:
        if session.query(Product).count() == 0:
            session.add_all(Product(**item) for item in DEMO_PRODUCTS)

        # На PostgreSQL явные/старые seed-id не двигают sequence. Выравниваем
        # её по максимуму, иначе следующая вставка без id получит конфликт.
        if engine.dialect.name == "postgresql":
            max_id = session.query(func.max(Product.id)).scalar()
            if max_id is not None:
                session.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence('products', 'id'), "
                        ":max_id, true)"
                    ),
                    {"max_id": int(max_id)},
                )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

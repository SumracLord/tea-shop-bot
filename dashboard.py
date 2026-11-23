"""
dashboard.py — аналитика продаж на Streamlit.

Читает те же таблицы, что и бот (SQLite локально или Supabase),
и показывает:
  * выручку, число заказов и средний чек;
  * выручку по дням;
  * популярность сортов чая;
  * последние заказы.

Запуск: python3.13 -m streamlit run dashboard.py
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import text

from config import DATABASE_BACKEND, engine, mask_database_url
from models import init_db

st.set_page_config(
    page_title="Аналитика чайного магазина",
    page_icon="🍵",
    layout="wide",
)

st.title("🍵 Дашборд интернет-магазина китайского чая")
st.caption(
    f"Источник данных: **{DATABASE_BACKEND}** · "
    f"`{mask_database_url()}` · кэш 30 секунд"
)

# Если дашборд запустили раньше бота — таблицы и демо-каталог создадутся здесь.
try:
    init_db()
except Exception:
    st.warning(
        "Не удалось инициализировать базу. Проверьте DATABASE_URL в .env "
        "и доступность Supabase."
    )


@st.cache_data(ttl=30, show_spinner=False)
def load_revenue_by_day() -> pd.DataFrame:
    """Выручка и количество заказов по дням."""
    query = text(
        """
        SELECT
            DATE(o.created_at) AS day,
            SUM(o.total_price) AS revenue,
            COUNT(o.id)        AS orders_count
        FROM orders o
        GROUP BY DATE(o.created_at)
        ORDER BY day
        """
    )
    frame = pd.read_sql(query, engine)
    if not frame.empty:
        frame["day"] = pd.to_datetime(frame["day"])
        frame["revenue"] = frame["revenue"].astype(float)
        frame["orders_count"] = frame["orders_count"].astype(int)
    return frame


@st.cache_data(ttl=30, show_spinner=False)
def load_product_popularity() -> pd.DataFrame:
    """Сколько единиц каждого сорта продано и на какую сумму."""
    query = text(
        """
        SELECT
            p.name                     AS product_name,
            SUM(oi.quantity)           AS sold_qty,
            SUM(oi.quantity * p.price) AS revenue
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        GROUP BY p.name
        ORDER BY sold_qty DESC
        """
    )
    frame = pd.read_sql(query, engine)
    if not frame.empty:
        frame["sold_qty"] = frame["sold_qty"].astype(float)
        frame["revenue"] = frame["revenue"].astype(float)
    return frame


@st.cache_data(ttl=30, show_spinner=False)
def load_last_orders(limit: int = 30) -> pd.DataFrame:
    """Последние заказы с покупателем."""
    query = text(
        """
        SELECT
            o.id          AS "№ заказа",
            u.first_name  AS "Покупатель",
            u.username    AS "Telegram",
            o.total_price AS "Сумма, ₽",
            o.status      AS "Статус",
            o.created_at  AS "Дата"
        FROM orders o
        JOIN users u ON u.telegram_id = o.user_id
        ORDER BY o.created_at DESC
        LIMIT :limit
        """
    )
    frame = pd.read_sql(query, engine, params={"limit": int(limit)})
    if not frame.empty:
        frame["Дата"] = pd.to_datetime(frame["Дата"])
        frame["Сумма, ₽"] = frame["Сумма, ₽"].astype(float)
    return frame


# ---------------------------------------------------------------------------
# Загрузка данных
# ---------------------------------------------------------------------------

try:
    revenue_df = load_revenue_by_day()
    popularity_df = load_product_popularity()
    orders_df = load_last_orders()
except Exception as exc:
    st.error(
        "Не удалось прочитать данные из базы. Проверьте DATABASE_URL в .env "
        "и доступность БД."
    )
    with st.expander("Технические подробности"):
        st.code(str(exc)[:1500])
    st.stop()


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

total_revenue = float(revenue_df["revenue"].sum()) if not revenue_df.empty else 0.0
total_orders = int(revenue_df["orders_count"].sum()) if not revenue_df.empty else 0
average_check = total_revenue / total_orders if total_orders else 0.0

col1, col2, col3 = st.columns(3)
col1.metric("💰 Общая выручка", f"{total_revenue:,.0f} ₽".replace(",", " "))
col2.metric("📦 Заказов", f"{total_orders}")
col3.metric("🧾 Средний чек", f"{average_check:,.0f} ₽".replace(",", " "))

st.divider()


# ---------------------------------------------------------------------------
# График выручки
# ---------------------------------------------------------------------------

st.subheader("📈 Выручка по дням")
if revenue_df.empty:
    st.info("Заказов пока нет — данные появятся после первых продаж.")
else:
    revenue_chart = (
        alt.Chart(revenue_df)
        .mark_line(point=True, color="#2e7d32", strokeWidth=2)
        .encode(
            x=alt.X("day:T", title="Дата"),
            y=alt.Y("revenue:Q", title="Выручка, ₽"),
            tooltip=[
                alt.Tooltip("day:T", title="Дата"),
                alt.Tooltip("revenue:Q", title="Выручка, ₽", format=",.0f"),
                alt.Tooltip("orders_count:Q", title="Заказов"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(revenue_chart, width="stretch")

    orders_chart = (
        alt.Chart(revenue_df)
        .mark_bar(color="#ef6c00", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("day:T", title="Дата"),
            y=alt.Y("orders_count:Q", title="Заказов"),
            tooltip=[
                alt.Tooltip("day:T", title="Дата"),
                alt.Tooltip("orders_count:Q", title="Заказов"),
            ],
        )
        .properties(height=200)
    )
    st.altair_chart(orders_chart, width="stretch")


# ---------------------------------------------------------------------------
# Популярность сортов
# ---------------------------------------------------------------------------

st.subheader("🫖 Популярность сортов чая")
if popularity_df.empty:
    st.info("Нет данных о проданных товарах.")
else:
    left, right = st.columns(2)

    with left:
        st.caption("Доля проданных единиц")
        pie = (
            alt.Chart(popularity_df)
            .mark_arc(innerRadius=55, outerRadius=110)
            .encode(
                theta=alt.Theta("sold_qty:Q", stack=True),
                color=alt.Color("product_name:N", legend=alt.Legend(title="Сорт")),
                tooltip=[
                    alt.Tooltip("product_name:N", title="Сорт"),
                    alt.Tooltip("sold_qty:Q", title="Продано, шт."),
                ],
            )
            .properties(height=300)
        )
        st.altair_chart(pie, width="stretch")

    with right:
        st.caption("Продано единиц")
        bars = (
            alt.Chart(popularity_df)
            .mark_bar(color="#2e7d32", cornerRadiusEnd=5)
            .encode(
                y=alt.Y("product_name:N", sort="-x", title=""),
                x=alt.X("sold_qty:Q", title="Штук"),
                tooltip=[
                    alt.Tooltip("product_name:N", title="Сорт"),
                    alt.Tooltip("sold_qty:Q", title="Продано, шт."),
                    alt.Tooltip("revenue:Q", title="Выручка, ₽", format=",.0f"),
                ],
            )
            .properties(height=300)
        )
        st.altair_chart(bars, width="stretch")

    with st.expander("Таблица продаж по сортам"):
        table = popularity_df.copy()
        table.columns = ["Сорт", "Продано, шт.", "Выручка, ₽"]
        st.dataframe(
            table,
            width="stretch",
            hide_index=True,
            column_config={
                "Выручка, ₽": st.column_config.NumberColumn(format="%.2f"),
            },
        )


# ---------------------------------------------------------------------------
# Последние заказы
# ---------------------------------------------------------------------------

st.subheader("🧾 Последние заказы")
if orders_df.empty:
    st.info("Заказов пока нет.")
else:
    st.dataframe(
        orders_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Сумма, ₽": st.column_config.NumberColumn(format="%.2f"),
            "Дата": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
        },
    )

st.divider()

with st.sidebar:
    st.header("Управление")
    if st.button("🔄 Обновить данные", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption(
        "Кэш — 30 секунд. Новый заказ появится в дашборде "
        "после обновления страницы или нажатия кнопки."
    )
    st.divider()
    st.caption(
        "Бот и дашборд читают одну и ту же базу. "
        "Локально это файл tea_shop.db, в облаке — Supabase PostgreSQL."
    )

st.caption("Данные обновляются при перезагрузке страницы; кэш — 30 секунд.")

# app_lemon.py
# レモン市場 (Akerlof) の教室実験用 Streamlit アプリ
# 元の app.py をベースにレモン市場版へ改修

# --- 0. Imports & Constants ---
import streamlit as st
import pandas as pd
import os
import random
import time

try:
    import psycopg2
    from psycopg2.extras import DictCursor
    from psycopg2.pool import SimpleConnectionPool
except ImportError:
    psycopg2 = None
    DictCursor = None
    SimpleConnectionPool = None

import sqlite3


# ---【重要】Renderデプロイ時の設定 ---
# requirements.txt:
#   streamlit, pandas, matplotlib, numpy, psycopg2-binary
# 環境変数:
#   DATABASE_URL (Postgres URL), ADMIN_PW (管理者パスワード)
# DATABASE_URL がなければローカルSQLite (local_lemon_market.db) を使用。
# ----------------------------------------------------


# --- 実験パラメータ ---
INITIAL_MONEY = 100
GOOD_SELLER_VALUE = 60   # 良品: 売り手(=保有者)の評価額
GOOD_BUYER_VALUE = 80    # 良品: 買い手(=非保有者)の評価額
LEMON_SELLER_VALUE = 20  # ポンコツ: 売り手の評価額
LEMON_BUYER_VALUE = 30   # ポンコツ: 買い手の評価額
MAX_PRICE = 100
TOTAL_ROUNDS = 2
DB_FILE = "local_lemon_market.db"


# --- 1. DB ユーティリティ ---

@st.cache_resource(show_spinner=False)
def get_pg_pool():
    db_url = os.environ.get('DATABASE_URL')
    if not (db_url and psycopg2 and SimpleConnectionPool):
        return None
    db_url = db_url.replace("postgres://", "postgresql://", 1)
    return SimpleConnectionPool(minconn=1, maxconn=10, dsn=db_url)


def connect():
    pool = get_pg_pool()
    if pool:
        return pool.getconn()
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=3000;")
    except Exception:
        pass
    return conn


def release(conn):
    pool = get_pg_pool()
    if pool and psycopg2 and isinstance(conn, psycopg2.extensions.connection):
        pool.putconn(conn)
    else:
        conn.close()


def get_cursor(conn):
    if psycopg2 and isinstance(conn, psycopg2.extensions.connection):
        return conn.cursor(cursor_factory=DictCursor)
    conn.row_factory = sqlite3.Row
    return conn.cursor()


def get_placeholder_char(conn):
    return "%s" if psycopg2 and isinstance(conn, psycopg2.extensions.connection) else "?"


def row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        try:
            return {k: row[k] for k in row.keys()}
        except Exception:
            return row


def rows_to_dicts(rows):
    return [row_to_dict(r) for r in rows]


def retry_on_db_lock(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "database is locked" in str(e):
                st.warning("DBがロックされました。少し待ってからリトライします。")
                time.sleep(1)
                return func(*args, **kwargs)
            raise e
    return wrapper


# --- 2. DB初期化 ---

@retry_on_db_lock
def initialize_db():
    conn = connect()
    c = get_cursor(conn)
    is_postgres = psycopg2 and isinstance(conn, psycopg2.extensions.connection)
    id_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN" if is_postgres else "INTEGER"
    default_bool = "FALSE" if is_postgres else "0"

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS lemon_players (
            id {id_type},
            name TEXT,
            class_name TEXT,
            money INTEGER,
            has_car {bool_type} DEFAULT {default_bool},
            car_type TEXT,
            acquired {bool_type} DEFAULT {default_bool},
            bid_or_ask INTEGER,
            submitted {bool_type} DEFAULT {default_bool},
            unit INTEGER DEFAULT 0,
            bought_type TEXT,
            payoff INTEGER
        )
    """)
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS lemon_group_info (
            id INTEGER PRIMARY KEY,
            round INTEGER,
            final_price INTEGER,
            confirmed {bool_type} DEFAULT {default_bool},
            show_result {bool_type} DEFAULT {default_bool},
            show_graph {bool_type} DEFAULT {default_bool}
        )
    """)
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS lemon_player_history (
            id {id_type},
            name TEXT,
            class_name TEXT,
            round INTEGER,
            role TEXT,
            car_type_before TEXT,
            acquired_before {bool_type},
            bid_or_ask INTEGER,
            unit INTEGER,
            bought_type TEXT,
            money INTEGER
        )
    """)

    c.execute("SELECT id FROM lemon_group_info WHERE id = 1")
    if c.fetchone() is None:
        c.execute(
            "INSERT INTO lemon_group_info (id, round, confirmed, show_result, show_graph) "
            "VALUES (1, 1, FALSE, FALSE, FALSE)"
        )

    c.execute("CREATE INDEX IF NOT EXISTS idx_lp_class ON lemon_players(class_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lp_class_sub ON lemon_players(class_name, submitted)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lp_name_class ON lemon_players(name, class_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lh_class_round ON lemon_player_history(class_name, round)")

    conn.commit()
    release(conn)


# --- 3. データアクセス (読み取り) ---

def load_player(student_id, class_name):
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    c.execute(
        f"SELECT * FROM lemon_players WHERE name = {p} AND class_name = {p}",
        (student_id, class_name)
    )
    result = row_to_dict(c.fetchone())
    release(conn)
    return result


@st.cache_data(ttl=5, show_spinner=False)
def load_group_info():
    conn = connect()
    c = get_cursor(conn)
    c.execute("SELECT * FROM lemon_group_info WHERE id=1")
    result = row_to_dict(c.fetchone())
    release(conn)
    return result


def load_all_players(class_name):
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    c.execute(f"SELECT * FROM lemon_players WHERE class_name = {p}", (class_name,))
    results = rows_to_dicts(c.fetchall())
    release(conn)
    return results


# --- 4. データアクセス (書き込み) ---

@retry_on_db_lock
def initialize_player(student_id, class_name):
    """登録時に乱数で半数に車を配り、車のタイプ(良/ポンコツ)も乱数で決める。
    DBに保存されるので、離脱して再ログインしても同じ役割が維持される。"""
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)

    has_car = random.random() < 0.5
    if has_car:
        car_type = "good" if random.random() < 0.5 else "lemon"
    else:
        car_type = None

    sql = (
        f"INSERT INTO lemon_players "
        f"(name, class_name, money, has_car, car_type, acquired, submitted) "
        f"VALUES ({p}, {p}, {p}, {p}, {p}, FALSE, FALSE)"
    )
    c.execute(sql, (student_id, class_name, INITIAL_MONEY, has_car, car_type))
    conn.commit()
    release(conn)


@retry_on_db_lock
def submit_player_decision(player_name, class_name, price):
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    sql = (
        f"UPDATE lemon_players SET bid_or_ask = {p}, submitted = TRUE "
        f"WHERE name = {p} AND class_name = {p}"
    )
    c.execute(sql, (price, player_name, class_name))
    conn.commit()
    release(conn)


# --- 5. 需給曲線計算 ---

def compute_demand_supply_curves(players):
    """各価格における需要(買い)・供給(売り)本数。
    買い手: bid_or_ask は最高買い値(bid)。需要 = bid >= price の人数。
    売り手: bid_or_ask は最低売り値(ask)。供給 = ask <= price の人数。
    """
    import numpy as np
    buy_hist = np.zeros(MAX_PRICE + 1, dtype=np.int32)
    sell_hist = np.zeros(MAX_PRICE + 1, dtype=np.int32)

    for pl in players:
        if not pl.get("submitted"):
            continue
        price = pl.get("bid_or_ask")
        if price is None:
            continue
        price = int(price)
        if price < 0 or price > MAX_PRICE:
            continue
        if pl.get("has_car"):
            sell_hist[price] += 1
        else:
            buy_hist[price] += 1

    demand = buy_hist[::-1].cumsum()[::-1]
    supply = sell_hist.cumsum()
    prices = np.arange(MAX_PRICE + 1)
    return prices, demand, supply


# --- 6. 市場清算 ---

@retry_on_db_lock
def clear_market(class_name):
    """市場を清算する。需給の交点で価格を決定 → マッチング → DB更新。"""
    import numpy as np
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)

    # 未提出者を不参加扱いに
    c.execute(
        f"UPDATE lemon_players SET submitted = TRUE, bid_or_ask = NULL "
        f"WHERE submitted = FALSE AND class_name = {p}",
        (class_name,)
    )
    conn.commit()

    players = load_all_players(class_name)

    prices_arr, demand_arr, supply_arr = compute_demand_supply_curves(players)
    trade_volume = np.minimum(demand_arr, supply_arr)

    if len(trade_volume) > 0 and trade_volume.max() > 0:
        max_vol = trade_volume.max()
        candidate_prices = np.where(trade_volume == max_vol)[0]
        # 取引量最大の価格帯の中央値を採用
        price = int(candidate_prices[len(candidate_prices) // 2])
    else:
        price = 0

    # マッチング: ask <= price の売り手を低い順、bid >= price の買い手を高い順に
    sellers = [
        pl for pl in players
        if pl.get("has_car")
        and pl.get("bid_or_ask") is not None
        and pl.get("bid_or_ask") <= price
    ]
    buyers = [
        pl for pl in players
        if not pl.get("has_car")
        and pl.get("bid_or_ask") is not None
        and pl.get("bid_or_ask") >= price
    ]

    sellers.sort(key=lambda x: x["bid_or_ask"])
    buyers.sort(key=lambda x: -x["bid_or_ask"])

    # 価格が同じ場合は良品/ポンコツが偏らないようにシャッフル
    # (ask価格が同じ売り手の中での順序をランダム化)
    def shuffle_within_groups(lst, key):
        from itertools import groupby
        out = []
        for _, group in groupby(lst, key=key):
            g = list(group)
            random.shuffle(g)
            out.extend(g)
        return out

    sellers = shuffle_within_groups(sellers, key=lambda x: x["bid_or_ask"])
    buyers = shuffle_within_groups(buyers, key=lambda x: -x["bid_or_ask"])

    n_trades = min(len(sellers), len(buyers))

    # 各プレイヤーの更新内容を準備
    updates = {}
    for pl in players:
        updates[pl["id"]] = {
            "unit": 0,
            "money": pl["money"],
            "has_car": pl.get("has_car"),
            "car_type": pl.get("car_type"),
            "acquired": pl.get("acquired"),
            "bought_type": None,
        }

    for i in range(n_trades):
        seller = sellers[i]
        buyer = buyers[i]
        car_type = seller["car_type"]

        updates[seller["id"]]["unit"] = -1
        updates[seller["id"]]["money"] = seller["money"] + price
        updates[seller["id"]]["has_car"] = False
        updates[seller["id"]]["car_type"] = None
        updates[seller["id"]]["acquired"] = False

        updates[buyer["id"]]["unit"] = 1
        updates[buyer["id"]]["money"] = buyer["money"] - price
        updates[buyer["id"]]["has_car"] = True
        updates[buyer["id"]]["car_type"] = car_type
        updates[buyer["id"]]["acquired"] = True
        updates[buyer["id"]]["bought_type"] = car_type

    # ラウンド番号取得
    c.execute("SELECT round FROM lemon_group_info WHERE id=1")
    round_num = (row_to_dict(c.fetchone()) or {}).get('round') or 1

    # DBに反映 + 履歴記録
    for pl in players:
        u = updates[pl["id"]]
        c.execute(
            f"UPDATE lemon_players SET unit = {p}, money = {p}, has_car = {p}, "
            f"car_type = {p}, acquired = {p}, bought_type = {p} WHERE id = {p}",
            (u["unit"], u["money"], u["has_car"], u["car_type"],
             u["acquired"], u["bought_type"], pl["id"])
        )
        role = "seller" if pl.get("has_car") else "buyer"
        c.execute(
            f"INSERT INTO lemon_player_history "
            f"(name, class_name, round, role, car_type_before, acquired_before, "
            f"bid_or_ask, unit, bought_type, money) "
            f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})",
            (pl["name"], pl["class_name"], round_num, role,
             pl.get("car_type"), pl.get("acquired"),
             pl.get("bid_or_ask"), u["unit"], u["bought_type"], u["money"])
        )

    c.execute(
        f"UPDATE lemon_group_info SET final_price = {p}, show_result = TRUE, "
        f"show_graph = TRUE WHERE id = 1",
        (price,)
    )
    conn.commit()
    release(conn)
    return price


def compute_payoff(player):
    """最終 payoff = money + 保有車の価値
    保有車の価値:
      - 元から持っていた車 (acquired=False) → 売り手評価額 (60 or 20)
      - 買って手に入れた車 (acquired=True) → 買い手評価額 (80 or 30)
    """
    money = player.get("money") or 0
    if not player.get("has_car"):
        return money
    car_type = player.get("car_type")
    acquired = player.get("acquired")
    if car_type == "good":
        return money + (GOOD_BUYER_VALUE if acquired else GOOD_SELLER_VALUE)
    elif car_type == "lemon":
        return money + (LEMON_BUYER_VALUE if acquired else LEMON_SELLER_VALUE)
    return money


@retry_on_db_lock
def confirm_results(class_name):
    """このラウンドの結果を確定。最終ラウンドなら payoff を計算。"""
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)

    c.execute("UPDATE lemon_group_info SET confirmed = TRUE WHERE id=1")
    c.execute("SELECT round FROM lemon_group_info WHERE id=1")
    round_num = (row_to_dict(c.fetchone()) or {}).get('round') or 1

    if round_num >= TOTAL_ROUNDS:
        players = load_all_players(class_name)
        for pl in players:
            payoff = compute_payoff(pl)
            c.execute(
                f"UPDATE lemon_players SET payoff = {p} WHERE id = {p}",
                (payoff, pl["id"])
            )

    conn.commit()
    release(conn)


@retry_on_db_lock
def next_round():
    conn = connect()
    c = get_cursor(conn)
    c.execute(
        "UPDATE lemon_group_info SET round = round + 1, final_price = NULL, "
        "confirmed = FALSE, show_result = FALSE, show_graph = FALSE"
    )
    c.execute(
        "UPDATE lemon_players SET submitted = FALSE, bid_or_ask = NULL, "
        "unit = 0, bought_type = NULL"
    )
    conn.commit()
    release(conn)


@retry_on_db_lock
def reset_experiment():
    conn = connect()
    c = get_cursor(conn)
    if psycopg2 and isinstance(conn, psycopg2.extensions.connection):
        c.execute("TRUNCATE TABLE lemon_players, lemon_player_history RESTART IDENTITY")
    else:
        c.execute("DELETE FROM lemon_players")
        c.execute("DELETE FROM lemon_player_history")
    c.execute(
        "UPDATE lemon_group_info SET final_price=NULL, round=1, "
        "confirmed=FALSE, show_result=FALSE, show_graph=FALSE WHERE id=1"
    )
    conn.commit()
    release(conn)
    if "student_id" in st.session_state:
        del st.session_state["student_id"]


# --- 7. グラフ描画 ---

def plot_market_curves(prices, demand, supply, final_price=None):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot(demand, prices, label="Demand (Buyers)", drawstyle="steps-post")
    ax.plot(supply, prices, label="Supply (Sellers)", drawstyle="steps-post")
    if final_price is not None:
        ax.axhline(y=final_price, linestyle='--', color='red',
                   label=f'Price = {final_price}')
    ax.set_xlabel("Quantity")
    ax.set_ylabel("Price")
    ax.set_title("Lemon Market: Demand and Supply")
    ax.legend()
    ax.grid(True)
    return fig


@st.cache_data(show_spinner=False, ttl=5)
def cached_curves(class_name):
    players = load_all_players(class_name)
    prices, demand, supply = compute_demand_supply_curves(players)
    return prices.tolist(), demand.tolist(), supply.tolist()


def render_market_graph(class_name, final_price=None):
    import numpy as np
    prices, demand, supply = cached_curves(class_name)
    fig = plot_market_curves(np.array(prices), np.array(demand),
                              np.array(supply), final_price)
    st.pyplot(fig)


# --- 8. 集計 ---

def get_trade_summary(class_name):
    """全ラウンド累計の良品/ポンコツ取引数。"""
    conn = connect()
    p = get_placeholder_char(conn)
    df = pd.read_sql_query(
        f"SELECT bought_type, COUNT(*) as n FROM lemon_player_history "
        f"WHERE class_name = {p} AND unit = 1 GROUP BY bought_type",
        conn,
        params=(class_name,)
    )
    release(conn)
    summary = {"good": 0, "lemon": 0}
    for _, row in df.iterrows():
        if row["bought_type"] in summary:
            summary[row["bought_type"]] = int(row["n"])
    return summary


# --- 9. UI ヘルパ ---

def car_type_label(t):
    if t == "good":
        return "良品 ✨"
    elif t == "lemon":
        return "ポンコツ 💨"
    return "なし"


# --- 10. プレイヤー UI ---

def show_player_ui(class_name):
    st.subheader("プレイヤーログイン")
    student_id = st.text_input(
        "学籍番号を入力してください",
        st.session_state.get("student_id", "")
    )

    if not student_id:
        st.info("学籍番号を入力して実験に参加してください。")
        return

    st.session_state.student_id = student_id
    ensure_db()
    player = load_player(student_id, class_name)
    group_info = load_group_info()

    if player and st.query_params.get("id") != student_id:
        st.query_params["id"] = student_id
        st.rerun()

    if not player:
        round_num = group_info.get('round') or 1
        if group_info.get('confirmed') and round_num >= TOTAL_ROUNDS:
            st.error("このセッションは既に終了しています。")
            return
        if st.button("実験に参加登録する"):
            initialize_player(student_id, class_name)
            st.success(f"ようこそ、{student_id}さん!登録が完了しました。")
            time.sleep(1)
            st.rerun()
        return

    # プレイヤーの状態
    round_num = group_info.get('round', 1)
    has_car = player.get("has_car")
    car_type = player.get("car_type")
    role = "売り手" if has_car else "買い手"

    st.info(f"ようこそ、{student_id} さん")
    st.markdown(f"**ラウンド {round_num} / {TOTAL_ROUNDS}**")
    st.markdown(f"**所持金:** {player['money']} 円")
    st.markdown(f"**あなたの立場:** {role}")

    if has_car:
        sv = GOOD_SELLER_VALUE if car_type == "good" else LEMON_SELLER_VALUE
        st.markdown(f"**保有している車:** {car_type_label(car_type)}")
        st.caption(
            f"あなたにとってこの車の価値は **{sv}円** です。"
            "これより安く売ると損になります。"
        )
    else:
        st.markdown("**車は保有していません**(これから買えます)")
        st.caption(
            f"市場には良品とポンコツが混在しています。"
            f"良品ならあなたにとって価値 **{GOOD_BUYER_VALUE}円**、"
            f"ポンコツなら **{LEMON_BUYER_VALUE}円** です。"
            "ただし、買うまで品質は分かりません。"
        )

    # --- 状態に応じたUI ---
    # 1. 提出済み・結果待ち
    if player['submitted'] and not group_info.get('show_result'):
        st.info("あなたの決定は提出済みです。管理者が市場を清算するまでお待ちください。")
        if st.button("結果を更新する", type="primary"):
            st.rerun()

    # 2. 結果表示
    elif group_info.get('show_result'):
        st.header("📈 市場結果")
        final_price = group_info.get('final_price')
        st.metric("市場価格", f"{final_price} 円")

        unit = player.get('unit', 0)
        bought_type = player.get('bought_type')

        if unit == 1:
            st.success(f"✅ あなたは車を **{final_price}円** で購入しました!")
            st.markdown(f"### 🎁 あなたが買ったのは… **{car_type_label(bought_type)}** でした!")
            if bought_type == "good":
                st.balloons()
                st.success(
                    f"良品でした!あなたにとってこの車は "
                    f"**{GOOD_BUYER_VALUE}円** の価値があります。"
                )
            else:
                st.warning(
                    f"ポンコツでした… あなたにとってこの車は "
                    f"**{LEMON_BUYER_VALUE}円** の価値しかありません。"
                )
        elif unit == -1:
            st.success(f"✅ あなたは車を **{final_price}円** で売却しました!")
        elif player.get('bid_or_ask') is None:
            st.info("あなたはこのラウンドに参加しませんでした。")
        else:
            # 提出はしたが取引不成立
            # 注意: この時点では updates 適用後なので、has_car は更新後の値
            # 「元々売り手だった」かは car_type の有無では判定不能なので
            # bid_or_ask + (前ラウンドからの状態) で大まかに表示
            if has_car:
                st.info("あなたの売り注文は成立しませんでした(価格が高すぎた / 買い手不足)。")
            else:
                st.info("あなたの買い注文は成立しませんでした(価格が低すぎた / 売り手不足)。")

        if group_info.get('show_graph'):
            st.subheader("📊 市場全体の需給曲線")
            render_market_graph(class_name, final_price)

        # 累計集計
        summary = get_trade_summary(class_name)
        st.subheader("📦 これまでの累計取引")
        col1, col2 = st.columns(2)
        col1.metric("良品の成立取引数", summary.get("good", 0))
        col2.metric("ポンコツの成立取引数", summary.get("lemon", 0))

        if group_info.get('confirmed'):
            if round_num >= TOTAL_ROUNDS:
                st.subheader("🎉 最終結果")
                st.metric("最終 Payoff", f"{player.get('payoff', 0)} 円")
                st.caption("payoff = 所持金 + 保有車のあなたにとっての価値")
            else:
                st.info("管理者が次のラウンドを開始するまでお待ちください...")
        else:
            st.info("管理者が結果を確定するまでお待ちください。")

        if st.button("状況を更新する"):
            st.rerun()

    # 3. 未提出
    else:
        st.header("🛒 取引入力")

        if has_car:
            st.subheader("📤 売却価格を決定")
            label = "最低売り値 (これ以上の価格でなら売る)"
            default = int(player.get("bid_or_ask") or
                          (GOOD_SELLER_VALUE if car_type == "good" else LEMON_SELLER_VALUE))
        else:
            st.subheader("📥 購入価格を決定")
            label = "最高買い値 (これ以下の価格でなら買う)"
            default = int(player.get("bid_or_ask") or 30)

        with st.form("order_form", clear_on_submit=False):
            price = st.slider(label, 0, MAX_PRICE, default, key="price_input")
            submitted = st.form_submit_button("決定を提出する", type="primary")

        if submitted:
            submit_player_decision(student_id, class_name, price)
            st.success("提出しました!")
            time.sleep(0.5)
            st.rerun()


# --- 11. 管理者 UI ---

def show_admin_ui(class_name):
    st.header(f"🔐 管理者モード (クラス: {class_name})")
    ensure_db()

    group_info = load_group_info()
    players = load_all_players(class_name)
    submitted_players = [p for p in players if p.get("submitted")]

    sellers = [p for p in players if p.get("has_car")]
    buyers = [p for p in players if not p.get("has_car")]

    st.subheader("現在の状況")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ラウンド", f"{group_info.get('round', 1)} / {TOTAL_ROUNDS}")
    col2.metric("参加人数", len(players))
    col3.metric("売り手 / 買い手", f"{len(sellers)} / {len(buyers)}")
    col4.metric("提出済み", f"{len(submitted_players)} / {len(players)}")

    good_sellers = sum(1 for p in sellers if p.get("car_type") == "good")
    lemon_sellers = sum(1 for p in sellers if p.get("car_type") == "lemon")
    st.caption(f"市場の在庫: 良品 {good_sellers} 台 / ポンコツ {lemon_sellers} 台")

    st.subheader("📈 市場操作")
    final_price = group_info.get('final_price')
    confirmed = group_info.get('confirmed')
    round_num = group_info.get('round', 1)

    if final_price is None:
        st.info("参加者の提出を待っています。全員が提出したら市場を清算してください。")
        if st.button("市場を清算し、価格を決定する", type="primary"):
            price = clear_market(class_name)
            st.success(f"市場価格は {price} 円に決定されました。")
            time.sleep(1)
            st.rerun()
    else:
        st.success(f"市場は清算済みです。決定価格: {final_price} 円")
        if not confirmed:
            if st.button("結果を確定する"):
                confirm_results(class_name)
                st.success("結果を確定しました。")
                time.sleep(1)
                st.rerun()
        else:
            st.info("このラウンドは確定済みです。")
            if round_num < TOTAL_ROUNDS:
                if st.button("次のラウンドへ進む"):
                    next_round()
                    st.success("次のラウンドに進みました。")
                    time.sleep(1)
                    st.rerun()
            else:
                st.info("最終ラウンドが終了しました。")

    st.subheader("📊 リアルタイムデータ")
    if players:
        render_market_graph(class_name, final_price)

        summary = get_trade_summary(class_name)
        col1, col2 = st.columns(2)
        col1.metric("累計: 良品の取引数", summary.get("good", 0))
        col2.metric("累計: ポンコツの取引数", summary.get("lemon", 0))

        df_players = pd.DataFrame(players)
        display_cols = ['name', 'has_car', 'car_type', 'acquired', 'bid_or_ask',
                        'unit', 'bought_type', 'money', 'payoff', 'submitted']
        st.dataframe(
            df_players[[col for col in display_cols if col in df_players.columns]],
            use_container_width=True
        )
    else:
        st.info("まだ参加者がいません。")

    st.sidebar.header("実験制御")
    if st.sidebar.button("🔄 画面を更新"):
        st.rerun()
    if st.sidebar.button("⚠️ 実験をリセット", help="すべてのデータが削除されます!"):
        reset_experiment()
        st.sidebar.success("実験をリセットしました。")
        time.sleep(1)
        st.rerun()

    st.sidebar.header("📦 履歴ダウンロード")
    st.sidebar.download_button(
        "履歴CSVをダウンロード",
        data=history_csv_blob(class_name),
        file_name=f"lemon_history_{class_name}_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )


@st.cache_data(show_spinner=False, ttl=30)
def history_csv_blob(class_name):
    conn = connect()
    p = get_placeholder_char(conn)
    df = pd.read_sql_query(
        f"SELECT * FROM lemon_player_history WHERE class_name = {p}",
        conn,
        params=(class_name,)
    )
    release(conn)
    return df.to_csv(index=False).encode("utf-8")


# --- 12. メイン ---

@st.cache_resource(show_spinner=False)
def ensure_db():
    initialize_db()
    return True


def main():
    st.set_page_config(page_title="レモン市場実験", layout="centered")
    st.title("🍋 レモン市場実験")

    query_params = st.query_params
    class_name = query_params.get("class")
    url_student_id = query_params.get("id")
    if url_student_id:
        st.session_state["student_id"] = url_student_id

    if not class_name:
        st.error("URLにクラス情報が含まれていません。例: `?class=A` をURLの末尾に追加してください。")
        return

    st.sidebar.title("⚙️ モード切替")
    if st.sidebar.checkbox("管理者モード", key="admin_mode_check"):
        password = st.sidebar.text_input("パスワード", type="password")
        admin_password = os.environ.get("ADMIN_PW")

        if admin_password and password == admin_password:
            show_admin_ui(class_name)
        elif password:
            st.sidebar.error("パスワードが違います。")
            st.sidebar.info("プレイヤーモードで表示します。")
            show_player_ui(class_name)
        else:
            show_player_ui(class_name)
    else:
        show_player_ui(class_name)


if __name__ == "__main__":
    main()

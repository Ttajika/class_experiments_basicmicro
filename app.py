# app.py
# PostgreSQL対応 & 手動更新 Final Version（最小改修パフォーマンス改善版）

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
# 1. `requirements.txt` に以下のライブラリを追加してください:
#    streamlit
#    pandas
#    matplotlib
#    numpy
#    psycopg2-binary  # PostgreSQLに接続するために必須
#
# 2. Renderの環境変数（Environment Variables）に以下を設定してください:
#    - KEY: `DATABASE_URL`
#      VALUE: RenderがPostgreSQLサービス作成時に自動で提供するURL
#
#    - KEY: `ADMIN_PW`
#      VALUE: あなたが設定する管理者パスワード
#
# 3. このコードは、環境変数 `DATABASE_URL` が存在する場合にPostgreSQLを使い、
#    存在しない場合はローカルテスト用に `local_market.db` というSQLiteファイルを使用します。
# ----------------------------------------------------


# --- Constants ---
# Experiment Parameters
INITIAL_MONEY = 485
ENDOWMENT_MULTIPLIER = 111
MAX_PRICE = 300
MAX_UNITS = 5
PRICE_RANGE = range(0, MAX_PRICE + 1)


# --- 1. データベース・ユーティリティ（接続プール＆release導入） ---

@st.cache_resource(show_spinner=False)
def get_pg_pool():
    """
    Postgres接続プールを作成（存在すれば再利用）。
    """
    db_url = os.environ.get('DATABASE_URL')
    if not (db_url and psycopg2 and SimpleConnectionPool):
        return None
    db_url = db_url.replace("postgres://", "postgresql://", 1)
    # プランに応じてmaxconnは調整してください
    return SimpleConnectionPool(minconn=1, maxconn=10, dsn=db_url)

def connect():
    """
    RenderのPostgreSQLまたはローカルのSQLiteに接続する。
    PostgreSQL時はプールから取得、SQLite時はWAL+busy_timeoutを設定。
    """
    pool = get_pg_pool()
    if pool:
        return pool.getconn()
    else:
        conn = sqlite3.connect("local_market.db", check_same_thread=False)
        # SQLite のロック耐性を上げる
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=3000;")
        except Exception:
            pass
        return conn

def release(conn):
    """
    conn.close() の代わりに呼ぶ。Postgresはプールに返却、SQLiteはclose。
    """
    pool = get_pg_pool()
    if pool and psycopg2 and isinstance(conn, psycopg2.extensions.connection):
        pool.putconn(conn)
    else:
        conn.close()

def get_cursor(conn):
    """DBの種類に応じて適切なカーソルを返す (列名でアクセス可能にする)"""
    if psycopg2 and isinstance(conn, psycopg2.extensions.connection):
        return conn.cursor(cursor_factory=DictCursor)
    else:  # sqlite3.Connection
        conn.row_factory = sqlite3.Row
        return conn.cursor()

def get_placeholder_char(conn):
    """DBの種類に応じたプレースホルダ文字 (%s or ?) を返す"""
    return "%s" if psycopg2 and isinstance(conn, psycopg2.extensions.connection) else "?"

def row_to_dict(row):
    """psycopg2のDictRow / sqlite3.Row を素のdictに正規化"""
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
    """
    データベースがロックされている場合にリトライ処理を行うデコレータ。
    (SQLiteでのローカルテスト時にのみ意味を持つ)
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # SQLiteのロックエラーを想定
            if "database is locked" in str(e):
                st.warning("DBがロックされました。少し待ってからリトライします。")
                time.sleep(1)
                return func(*args, **kwargs)
            else:
                raise e
    return wrapper


@retry_on_db_lock
def initialize_db():
    """データベースとテーブルを初期化する（インデックス含む）"""
    conn = connect()
    c = get_cursor(conn)

    is_postgres = psycopg2 and isinstance(conn, psycopg2.extensions.connection)

    # データ型と自動インクリメントをDBに合わせて切り替え
    id_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN" if is_postgres else "INTEGER"
    default_bool = "FALSE" if is_postgres else "0"

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS players (
            id {id_type}, name TEXT, money INTEGER,
            endowment INTEGER, choice INTEGER, submitted {bool_type} DEFAULT {default_bool},
            payoff INTEGER, info INTEGER, class_name TEXT, qty INTEGER,
            unit INTEGER, mu1 INTEGER, mu2 INTEGER, mu3 INTEGER, mu4 INTEGER, mu5 INTEGER
        )
    """)
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS group_info (
            id INTEGER PRIMARY KEY, value INTEGER, final_price INTEGER,
            round INTEGER, confirmed {bool_type} DEFAULT {default_bool}, 
            show_result {bool_type} DEFAULT {default_bool},
            show_graph {bool_type} DEFAULT {default_bool}
        )
    """)
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS player_history (
            id {id_type}, name TEXT, round INTEGER,
            choice INTEGER, qty INTEGER, unit INTEGER DEFAULT 0, money INTEGER,
            endowment INTEGER, payoff INTEGER, info INTEGER, class_name TEXT
        )
    """)

    # group_infoに初期データがない場合のみINSERT
    c.execute("SELECT id FROM group_info WHERE id = 1")
    if c.fetchone() is None:
        c.execute(
            "INSERT INTO group_info (id, value, round, confirmed, show_result, show_graph) VALUES (1, 100, 1, FALSE, FALSE, FALSE)"
        )

    # インデックス（Postgres/SQLite共通でIF NOT EXISTS対応）
    c.execute("CREATE INDEX IF NOT EXISTS idx_players_class ON players(class_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_players_class_sub ON players(class_name, submitted)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_player_name_class ON players(name, class_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_history_class_round ON player_history(class_name, round)")

    conn.commit()
    release(conn)


# --- 2. データアクセス関数 (読み取り) ---

def load_player(student_id):
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    c.execute(f"SELECT * FROM players WHERE name = {p}", (student_id,))
    result = row_to_dict(c.fetchone())
    release(conn)
    return result

@st.cache_data(ttl=5, show_spinner=False)
def load_group_info():
    conn = connect()
    c = get_cursor(conn)
    c.execute("SELECT * FROM group_info WHERE id=1")
    result = row_to_dict(c.fetchone())
    release(conn)
    return result

def load_all_players(class_name):
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    c.execute(f"SELECT * FROM players WHERE class_name = {p}", (class_name,))
    results = rows_to_dicts(c.fetchall())
    release(conn)
    return results


# --- 3. データアクセス関数 (書き込み) ---

@retry_on_db_lock
def initialize_player(student_id, class_name):
    conn = connect()
    c = get_cursor(conn)
    
    c.execute("SELECT value FROM group_info WHERE id=1")
    row = row_to_dict(c.fetchone())
    group_value = (row.get('value') if row else 100) or 100
    prob_val = group_value / 100.0
    endowment = 3 #random.choices([1, 2, 3, 4], weights=[prob_val**3, prob_val**2, prob_val, 1])[0]
    money = INITIAL_MONEY #- ENDOWMENT_MULTIPLIER * endowment
    info = int(random.expovariate(1 / group_value)) if prob_val > 0 else 0

    p = get_placeholder_char(conn)
    sql = f"INSERT INTO players (name, money, endowment, submitted, info, class_name) VALUES ({p}, {p}, {p}, FALSE, {p}, {p})"
    c.execute(sql, (student_id, money, endowment, info, class_name))
    conn.commit()
    release(conn)

@retry_on_db_lock
def submit_player_decision(player_name, class_name, choice, qty, mu_values):
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    
    padded_mus = mu_values + [None] * (MAX_UNITS - len(mu_values))
    mu_columns_update = ', '.join([f"mu{i+1} = {p}" for i in range(MAX_UNITS)])
    
    query = f"UPDATE players SET choice = {p}, submitted = TRUE, qty = {p}, {mu_columns_update} WHERE name = {p} AND class_name = {p}"
    params = [choice, qty] + padded_mus + [player_name, class_name]
    c.execute(query, params)
    conn.commit()
    release(conn)

def _get_unit_demands(player, price):
    if player.get("choice") != 1: return 0
    return sum(1 for i in range(1, MAX_UNITS + 1) if player.get(f"mu{i}") is not None and player.get(f"mu{i}") >= price)

def _get_unit_supplies(player, price):
    if player.get("choice") != -1: return 0
    return sum(1 for i in range(1, MAX_UNITS + 1) if player.get(f"mu{i}") is not None and player.get(f"mu{i}") <= price)

@retry_on_db_lock
def set_payoffs(group_value, class_name):
    import numpy as np
    conn = connect()
    c = get_cursor(conn)
    p = get_placeholder_char(conn)
    
    # 未提出者を不参加（choice=0）として確定
    c.execute(f"UPDATE players SET choice = 0, qty = 0, submitted = TRUE WHERE submitted = FALSE AND class_name = {p}", (class_name,))
    conn.commit()

    players = load_all_players(class_name)

    # --- 高速化: 価格探索をヒストグラム×累積和で ---
    prices_arr, demand_arr, supply_arr = compute_demand_supply_curves_fast(players)
    trade_volume = np.minimum(demand_arr, supply_arr)
    price = int(prices_arr[int(np.argmax(trade_volume))]) if len(trade_volume) > 0 else 0

    # 成立ユニットをマッチング（priceで閾値）
    buy_units = sorted(
        [(player.get(f"mu{i+1}"), player["id"])
         for player in players if player.get("choice") == 1
         for i in range(player.get("qty", 0))
         if player.get(f"mu{i+1}") is not None and player.get(f"mu{i+1}") >= price],
        reverse=True
    )
    sell_units = sorted(
        [(player.get(f"mu{i+1}"), player["id"])
         for player in players if player.get("choice") == -1
         for i in range(player.get("qty", 0))
         if player.get(f"mu{i+1}") is not None and player.get(f"mu{i+1}") <= price]
    )
    
    trades = min(len(buy_units), len(sell_units))
    matched_buyers, matched_sellers = {}, {}
    for i in range(trades):
        buyer_id = buy_units[i][1]
        seller_id = sell_units[i][1]
        matched_buyers[buyer_id] = matched_buyers.get(buyer_id, 0) + 1
        matched_sellers[seller_id] = matched_sellers.get(seller_id, 0) + 1

    c.execute("SELECT round FROM group_info WHERE id=1")
    row = row_to_dict(c.fetchone())
    round_num = row.get('round') if row else 1

    for player in players:
        unit = 0
        if player.get("choice") == 1:
            unit = matched_buyers.get(player["id"], 0)
        elif player.get("choice") == -1:
            unit = -matched_sellers.get(player["id"], 0)

        money = player["money"] - unit * price
        endowment = player["endowment"] + unit
        payoff = int(group_value * endowment + money)

        c.execute(f"UPDATE players SET unit = {p}, money = {p}, endowment = {p}, payoff = {p} WHERE id = {p}",
                  (unit, money, endowment, payoff, player["id"]))
        c.execute(
            f"INSERT INTO player_history (name, round, choice, qty, unit, money, endowment, payoff, info, class_name) "
            f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})",
            (player["name"], round_num, player.get("choice"), player.get("qty", 0), unit,
             money, endowment, payoff, player.get("info"), player.get("class_name"))
        )

    c.execute(f"UPDATE group_info SET final_price={p}, show_result=TRUE, show_graph=TRUE WHERE id=1", (price,))
    conn.commit()
    release(conn)
    return price

@retry_on_db_lock
def next_round():
    conn = connect()
    c = get_cursor(conn)
    c.execute("UPDATE group_info SET round = round + 1, final_price = NULL, confirmed = FALSE, show_result = FALSE, show_graph = FALSE")
    c.execute("UPDATE players SET submitted=FALSE, payoff=NULL, unit=NULL, choice=NULL, qty=NULL, mu1=NULL, mu2=NULL, mu3=NULL, mu4=NULL, mu5=NULL")
    conn.commit()
    release(conn)

@retry_on_db_lock
def confirm_results():
    conn = connect()
    c = get_cursor(conn)
    c.execute("UPDATE group_info SET confirmed = TRUE WHERE id=1")
    conn.commit()
    release(conn)

@retry_on_db_lock
def reset_experiment():
    new_value = random.randint(80, 200)
    conn = connect()
    c = get_cursor(conn)
    if psycopg2 and isinstance(conn, psycopg2.extensions.connection):
        c.execute("TRUNCATE TABLE players, player_history RESTART IDENTITY")
    else:
        c.execute("DELETE FROM players")
        c.execute("DELETE FROM player_history")  # SQLite does not support TRUNCATE on multiple tables
    
    p = get_placeholder_char(conn)
    c.execute(f"UPDATE group_info SET final_price=NULL, round=1, value={p}, confirmed=FALSE, show_result=FALSE, show_graph=FALSE", (new_value,))
    conn.commit()
    release(conn)
    if "student_id" in st.session_state:
        del st.session_state["student_id"]


# --- 4. 高速コアロジック & グラフ描画（キャッシュ付） ---

def compute_demand_supply_curves_fast(players):
    """
    各価格での需要・供給本数をヒストグラム＋累積和で計算（O(N×U + 価格数)）
    """
    import numpy as np
    buy_hist  = np.zeros(MAX_PRICE + 1, dtype=np.int32)
    sell_hist = np.zeros(MAX_PRICE + 1, dtype=np.int32)

    for p in players:
        qty = int(p.get("qty") or 0)
        ch  = p.get("choice")
        if ch == 1:  # 購入
            for i in range(1, qty + 1):
                mu = p.get(f"mu{i}")
                if mu is not None:
                    buy_hist[int(mu)] += 1
        elif ch == -1:  # 売却
            for i in range(1, qty + 1):
                mu = p.get(f"mu{i}")
                if mu is not None:
                    sell_hist[int(mu)] += 1

    # 需要: mu >= price → 右からの累積和
    demand = buy_hist[::-1].cumsum()[::-1]
    # 供給: mu <= price → 左からの累積和
    supply = sell_hist.cumsum()

    prices = np.arange(MAX_PRICE + 1)
    return prices, demand, supply

def plot_market_curves_from_arrays(prices, demand, supply, final_price=None):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot(demand, prices, label="Demand", drawstyle="steps-post")
    ax.plot(supply, prices, label="Supply", drawstyle="steps-post")
    if final_price is not None:
        ax.axhline(y=final_price, linestyle='--', label=f'Price = {final_price}')
    ax.set_xlabel("Quantity")
    ax.set_ylabel("Price")
    ax.set_title("Market Demand and Supply")
    ax.legend()
    ax.grid(True)
    return fig

@st.cache_data(show_spinner=False, ttl=5)
def cached_curves(class_name):
    players = load_all_players(class_name)
    prices, demand, supply = compute_demand_supply_curves_fast(players)
    # numpy配列はそのまま返せないのでlist化
    return prices.tolist(), demand.tolist(), supply.tolist()

def render_market_graph(class_name, final_price=None):
    import numpy as np

    prices, demand, supply = cached_curves(class_name)
    fig = plot_market_curves_from_arrays(np.array(prices), np.array(demand), np.array(supply), final_price)
    st.pyplot(fig)


# --- 5. UIコンポーネント ---

def show_player_ui(class_name):
    st.subheader("プレイヤーログイン")
    student_id = st.text_input("学籍番号を入力してください", st.session_state.get("student_id", ""))

    if not student_id:
        st.info("学籍番号を入力して実験に参加してください。")
        return

    st.session_state.student_id = student_id
    ensure_db()
    player = load_player(student_id)
    group_info = load_group_info()

    if player and st.query_params.get("id") != student_id:
        st.query_params["id"] = student_id   # 1.30+（これでURLが更新されて再実行されます）
        st.rerun()

    if not player:
        if group_info.get('confirmed'):
            st.error("このセッションは既に終了しています。")
            return
        if st.button("実験に参加登録する"):
            initialize_player(student_id, class_name)
            st.success(f"ようこそ、{student_id}さん！ 登録が完了しました。")
            time.sleep(1)
            st.rerun()
        return

    st.info(f"ようこそ、{student_id} さん")
    st.markdown(f"**ラウンド {group_info.get('round', 1)}｜所持金:** {player['money']}円 ｜ **商品:** {player['endowment']}個")
    st.markdown(f"🧠 あなたの情報 (info): **{player['info']}**")

    # --- 状態に応じたUI表示 ---
    # 1. 提出済みで、結果待ちの状態 (手動更新)
    if player['submitted'] and not group_info.get('show_result'):
        st.info("あなたの決定は提出済みです。管理者が市場を清算するまでお待ちください。")
        if st.button("結果を更新する", type="primary"):
            st.rerun()
        st.info("管理者が操作した後、上のボタンを押すと結果が表示されます。")

    # 2. 結果表示の状態
    elif group_info.get('show_result'):
        st.header("📈 市場結果")
        final_price = group_info.get('final_price')
        st.metric("市場価格", f"{final_price} 円")

        unit = player.get('unit', 0)
        if unit > 0: st.success(f"あなたは {unit}個 の商品を購入しました。")
        elif unit < 0: st.warning(f"あなたは {abs(unit)}個 の商品を売却しました。")
        elif player['choice'] == 0: st.info("あなたはこのラウンドの取引に参加しませんでした。")
        else: st.info("あなたの注文は成立しませんでした。")

        if group_info.get('show_graph'):
            render_market_graph(class_name, final_price)

        if group_info.get('confirmed'):
            st.subheader("🎉 最終結果")
            st.metric("最終利得 (Payoff)", f"{player.get('payoff', 0)} 円")
            st.markdown(f"**最終資産:** {player.get('endowment', 0)}個 | {player.get('money', 0)}円")
        else:
            st.info("管理者が報酬を確定するまでお待ちください。")
        
        st.info("管理者が次のラウンドを開始するまでお待ちください...")
        if st.button("状況を更新する"):
            st.rerun()

    # 3. 未提出の状態
    else:
        st.header("🛒 取引入力")

        # --- Step 1: 取引種別と数量（ここは再実行されますが1回だけ想定） ---
        trade_type = st.radio("取引の種類を選択:", ["購入", "売却"], horizontal=True, key="trade_type")

        if trade_type == "購入":
            qty_limit = MAX_UNITS
            st.subheader("📥 購入希望の入力")
        else:
            if player['endowment'] == 0:
                st.warning("売却できる商品がありません。")
                return
            qty_limit = player['endowment']
            st.subheader("📤 売却希望の入力")

        max_qty = st.slider(
            "数量を決めてください（次のステップで各個の評価額を入力）",
            0, qty_limit, min(qty_limit, st.session_state.get("max_qty", 0)),
            key="max_qty"
        )

        # --- Step 2: フォームで“まとめて”入力（ここではスライダーを動かしても再実行されません） ---
        if max_qty > 0:
            with st.form("order_form", clear_on_submit=False):
                st.caption("各個の評価額（購入なら『支払ってよい上限』、売却なら『最低売りたい価格』）")
                mu_values = []
                # trade_typeごとに別キーにして衝突回避
                mu_key_prefix = "buy_mu_" if trade_type == "購入" else "sell_loss_"
                default_val = 100

                for i in range(1, max_qty + 1):
                    key = f"{mu_key_prefix}{i}"
                    val = st.slider(
                        f"{i}個目の評価額", 0, MAX_PRICE,
                        value=st.session_state.get(key, default_val),
                        key=key
                    )
                    mu_values.append(val)

                submitted = st.form_submit_button("決定を提出する", type="primary")

            # --- Step 3: 送信時だけDBを書き、再実行（=画面更新） ---
            if submitted:
                choice = 1 if trade_type == "購入" else -1
                submit_player_decision(student_id, class_name, choice, len(mu_values), mu_values)
                st.success("提出しました！")
                time.sleep(0.5)
                st.rerun()


def show_admin_ui(class_name):
    st.header(f"🔐 管理者モード (クラス: {class_name})")
    ensure_db()

    group_info = load_group_info()
    players = load_all_players(class_name)
    submitted_players = [p for p in players if p.get("submitted")]

    st.subheader("現在の状況")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ラウンド", group_info.get('round', 1))
    col2.metric("グループ価値", group_info.get('value', 'N/A'))
    col3.metric("参加人数", len(players))
    col4.metric("提出済み", f"{len(submitted_players)} / {len(players)}")

    st.subheader("📈 市場操作")
    final_price = group_info.get('final_price')
    confirmed = group_info.get('confirmed')

    if final_price is None:
        st.info("参加者の提出を待っています。全員が提出したら市場を清算してください。")
        if st.button("市場を清算し、価格を決定する", type="primary"):
            price = set_payoffs(group_info['value'], class_name)
            st.success(f"市場価格は {price} 円に決定されました。")
            time.sleep(1)
            st.rerun()
    else:
        st.success(f"市場は清算済みです。決定価格: {final_price}円")
        if not confirmed:
            if st.button("最終報酬を確定する"):
                confirm_results()
                st.success("報酬を確定しました。プレイヤー画面に最終結果が表示されます。")
                time.sleep(1)
                st.rerun()
        else:
            st.info("このラウンドの報酬は確定済みです。")

        if st.button("次のラウンドへ進む"):
            next_round()
            st.success("次のラウンドに進みました。")
            time.sleep(1)
            st.rerun()

    st.subheader("📊 リアルタイムデータ")
    if players:
        render_market_graph(class_name, final_price)
        df_players = pd.DataFrame(players)
        # DataFrameの列を整形
        display_cols = ['name', 'choice', 'qty', 'unit', 'money', 'endowment', 'payoff', 'info', 'submitted']
        st.dataframe(df_players[[col for col in display_cols if col in df_players.columns]], use_container_width=True)
    else:
        st.info("まだ参加者がいません。")

    st.sidebar.header("実験制御")
    if st.sidebar.button("🔄 画面を更新"):
        st.rerun()
    if st.sidebar.button("⚠️ 実験をリセット", help="すべてのデータが削除されます！"):
        reset_experiment()
        st.sidebar.success("実験をリセットしました。")
        time.sleep(1)
        st.rerun()
    
    st.sidebar.header("📦 履歴ダウンロード")
    st.sidebar.download_button(
        "履歴CSVをダウンロード",
        data=history_csv_blob(class_name),
        file_name=f"history_{class_name}_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )


# --- 6. 補助: 履歴CSVのキャッシュ ---

@st.cache_data(show_spinner=False, ttl=30)
def history_csv_blob(class_name):
    conn = connect()
    p = get_placeholder_char(conn)
    df = pd.read_sql_query(f"SELECT * FROM player_history WHERE class_name = {p}", conn, params=(class_name,))
    release(conn)
    return df.to_csv(index=False).encode("utf-8")


# --- 7. メイン処理（DB初期化は一度だけ） ---

@st.cache_resource(show_spinner=False)
def ensure_db():
    initialize_db()
    return True

def main():
    st.set_page_config(page_title="市場実験", layout="centered")
    st.title("ようこそ、市場実験へ！")

    # DBが存在しない場合、この時点で初期化（1回のみ）

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
        elif password: # パスワードが入力されたが、間違っている場合
            st.sidebar.error("パスワードが違います。")
            st.sidebar.info("プレイヤーモードで表示します。")
            show_player_ui(class_name)
        else: # パスワードが未入力、またはADMIN_PWが未設定
            show_player_ui(class_name)
    else:
        show_player_ui(class_name)


if __name__ == "__main__":
    main()

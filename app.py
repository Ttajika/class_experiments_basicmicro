
# app.py
import streamlit as st
import pandas as pd
import sqlite3
import os
import random
import matplotlib.pyplot as plt
import time

# --- ページ設定 ---
st.set_page_config(page_title="市場実験", layout="centered")
DB_PATH = "market.db"

# --- DB接続 ---
def connect():
    return sqlite3.connect(DB_PATH)

def initialize_db():
    conn = connect()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            money INTEGER,
            endowment INTEGER,
            bid INTEGER,
            qty INTEGER,
            choice INTEGER,
            payoff INTEGER,
            submitted INTEGER DEFAULT 0,
            info INTEGER,
            unit INTEGER,
            class_name TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_info (
            id INTEGER PRIMARY KEY,
            value INTEGER,
            final_price INTEGER,
            round INTEGER,
            confirmed INTEGER DEFAULT 0,
            show_result INTEGER DEFAULT 0,
            show_graph INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS player_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            round INTEGER,
            choice INTEGER,
            bid INTEGER,
            qty INTEGER,
            unit INTEGER,
            money INTEGER,
            endowment INTEGER,
            payoff INTEGER,
            info INTEGER,
            class_name TEXT
        )
    """)
    c.execute("INSERT OR IGNORE INTO group_info (id, value, round) VALUES (1, 100, 1)")
    conn.commit()
    conn.close()

def load_player(student_id):
    conn = connect()
    c = conn.cursor()
    result = c.execute("SELECT * FROM players WHERE name = ?", (student_id,)).fetchone()
    conn.close()
    if result:
        keys = ["id", "name", "money", "endowment", "bid", "qty", "choice", "payoff", "submitted", "info", "unit"]
        return dict(zip(keys, result))
    return None

def save_player(player):
    conn = connect()
    c = conn.cursor()
    c.execute("""
        UPDATE players SET bid=?, qty=?, choice=?, submitted=1
        WHERE name=?
    """, (player['bid'], player['qty'], player['choice'], player['name']))
    conn.commit()
    conn.close()

def initialize_player(student_id, class_name):
    conn = connect()
    c = conn.cursor()
    value = c.execute("SELECT value FROM group_info WHERE id=1").fetchone()
    if not value:
        value = 100
    else:
        value = value[0]/100
    endowment = random.choices([1, 2, 3, 4], weights=[value**3, value**2, value, 1])[0]
    money = 485 - 111 * endowment
    info = int(random.expovariate(1 / value))
    c.execute("""
        INSERT INTO players (name, money, endowment, submitted, info, class_name)
        VALUES (?, ?, ?, 0, ?, ?)
    """, (student_id, money, endowment, info, class_name))
    conn.commit()
    conn.close()
    return {"name": student_id, "money": money, "endowment": endowment, "submitted": False, "info": info}


def load_group_value():
    conn = connect()
    c = conn.cursor()
    result = c.execute("SELECT value FROM group_info WHERE id=1").fetchone()
    conn.close()
    return result[0] if result else 100

def load_round():
    conn = connect()
    c = conn.cursor()
    result = c.execute("SELECT round FROM group_info WHERE id=1").fetchone()
    conn.close()
    return result[0] if result else 1

def load_confirmation():
    conn = connect()
    c = conn.cursor()
    result = c.execute("SELECT confirmed FROM group_info WHERE id=1").fetchone()
    conn.close()
    return result[0] if result else 0

def confirm_results():
    conn = connect()
    c = conn.cursor()
    c.execute("UPDATE group_info SET confirmed = 1 WHERE id=1")
    conn.commit()
    conn.close()

def reset_experiment():
    new_value = random.randint(80, 200)
    conn = connect()
    c = conn.cursor()
    c.execute("DELETE FROM players")
    c.execute("UPDATE group_info SET final_price=NULL, round=1, value=?, confirmed=0, show_graph=0", (new_value,))
    conn.commit()
    conn.close()

def next_round():
    conn = connect()
    c = conn.cursor()
    c.execute("UPDATE group_info SET round = round + 1, final_price = NULL, confirmed = 0, show_graph = 0")
    c.execute("UPDATE players SET submitted=0, bid=NULL, qty=NULL, choice=NULL")
    conn.commit()
    conn.close()

def set_payoffs(players, value, class_name, round_num):
    price = -1
    demand, supply = 1000, 0

    while supply < demand and price < 300:
        price += 1
        demand, supply = 0, 0
        for p in players:
            if p["choice"] == 1 and p["bid"] >= price:
                demand += p["qty"]
            elif p["choice"] == -1 and p["bid"] <= price:
                supply += p["qty"]

    conn = connect()
    c = conn.cursor()
    for p in players:
        unit = 0
        if p["choice"] == 1 and p["bid"] >= price:
            unit = p["qty"]
        elif p["choice"] == -1 and p["bid"] <= price:
            unit = -p["qty"]
        money = p["money"] - price * unit
        endowment = p["endowment"] + unit
        payoff = int(value * endowment + money)


        c.execute("""UPDATE players SET payoff=?, money=?, endowment=?, unit=? WHERE id=?""",
          (payoff, money, endowment, unit, p["id"]))
        c.execute("""
            INSERT INTO player_history (name, round, choice, bid, qty, unit, money, endowment, payoff, info, class_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (p["name"], round_num, p["choice"], p["bid"], p["qty"], unit, money, endowment, payoff, p["info"], class_name))


    c.execute("UPDATE group_info SET final_price=?, show_graph=1 WHERE id=1", (price,))
    conn.commit()
    conn.close()
    return price

def load_final_price():
    conn = connect()
    c = conn.cursor()
    result = c.execute("SELECT final_price FROM group_info WHERE id=1").fetchone()
    conn.close()
    return result[0] if result else None

def load_all_players():
    conn = connect()
    c = conn.cursor()
    results = c.execute("SELECT * FROM players").fetchall()
    keys = ["id", "name", "money", "endowment", "bid", "qty", "choice", "payoff", "submitted", "info", "unit"]
    conn.close()
    return [dict(zip(keys, row)) for row in results]

def compute_inverse_demand_supply(players):
    price_range = list(range(0, 201))
    demand, supply = [], []
    for p in price_range:
        d = sum(pl["qty"] for pl in players if pl["choice"] == 1 and pl["bid"] >= p)
        s = sum(pl["qty"] for pl in players if pl["choice"] == -1 and pl["bid"] <= p)
        demand.append(d)
        supply.append(s)
    return price_range, demand, supply



# --- プレイヤー画面 ---
def show_player_ui(class_name):
    if "student_id" not in st.session_state:
        st.session_state.student_id = ""

    st.subheader("ログイン")

    student_id = st.text_input("学籍番号を入力してください", value=st.session_state.student_id)
    if student_id:
        st.session_state.student_id = student_id


    if student_id:
        player = load_player(student_id)
        if not player:
            player = initialize_player(student_id, class_name)
            st.success(f"新しくプレイヤー {student_id} を登録しました")
            time.sleep(2)
            st.rerun()
        else:
            st.info(f"ようこそ、{student_id} さん")

        # 必要な情報取得
        try:
            round_num = load_round()
        except:
            round_num = 1

        try:
            group_value = load_group_value()
        except:
            group_value = 100

        confirmed = load_confirmation()
        st.markdown(f"**ラウンド {round_num}｜所持金:** {player['money']} 円　｜　**商品:** {player['endowment']} 個")
        st.markdown(f"🧠 あなたに与えられた情報（info）: **{player['info']}**")

        # 未提出でもchoiceがNoneのままにならないよう補正（次のラウンド開始時）
        # ※フォームに入る前に表示されないよう、提出済み扱いはしない
        if not confirmed and (player.get("choice") is None or player.get("bid") is None or player.get("qty") is None):
            player["choice"] = 0
            player["bid"] = 0
            player["qty"] = 0
            save_player(player)

        # リロードボタン
        if st.button("🔄 市場結果を再読み込みする"):
                st.rerun()

        if player.get("payoff") is not None:
                
                
                # 逆需要・供給関数グラフ描画
                conn = connect()
                c = conn.cursor()
                show_graph = c.execute("SELECT show_graph FROM group_info WHERE id=1").fetchone()
                if show_graph[0] == 1:
                    st.subheader("市場結果")
                    final_price = load_final_price()
                    st.markdown(f"🪙 市場価格: **{final_price} 円**")
                    if player.get("unit") > 0:
                        st.markdown(f"🛒 購入数量: {player['unit']} 個")
                    elif player.get("unit") < 0:
                        st.markdown(f"📤 売却数量: {abs(player['unit'])} 個")
                    elif player.get("unit") == 0:
                        st.markdown("⚠️このラウンドでは取引できませんでした。")
                    if player.get("choice") == 0:
                        st.markdown("⚠️ このラウンドでは取引に参加していません。")
                    players = load_all_players()
                    price_range = list(range(0, 201))
                    prices, demand, supply = compute_inverse_demand_supply(players)

                    fig, ax = plt.subplots()
                    ax.plot(demand, prices, label="D")
                    ax.plot(supply, prices, label="S")
                    ax.set_xlabel("Q")
                    ax.set_ylabel("P")
                    ax.set_title("Market Demmand and Supply")
                    ax.legend()
                    st.pyplot(fig)

                    if confirmed:
                        st.subheader("結果（報酬確定後）")
                        st.markdown(f"💰 **報酬:** {player['payoff']} 円")
                        st.markdown(f"📦 最終所持: {player['endowment']} 個 | 💵 {player['money']} 円")
                    else:
                        st.info("管理者が結果を確定するまで、報酬は表示されません。")
        conn = connect()
        c = conn.cursor()
        show_graph = c.execute("SELECT show_graph FROM group_info WHERE id=1").fetchone()
        if show_graph[0]==0:
            st.subheader("取引の入力")
            if not confirmed:
                with st.form("trade_form"):
                    choice = st.radio("取引選択", [1, -1], format_func=lambda x: "購入" if x == 1 else "売却")
                    bid = st.slider("希望価格（円）", min_value=0, max_value=200, step=1)
                    qty = st.slider("希望数量（個）", min_value=1, max_value=5, step=1)
                    submitted = st.form_submit_button("提出")

                    if submitted:
                        if choice == 1 and bid * qty > player['money']:
                            st.warning("所持金を超えています。")
                        elif choice == -1 and qty > player['endowment']:
                            st.warning("売却数量が多すぎます。")
                        else:
                            player.update({
                                "choice": choice,
                                "bid": bid,
                                "qty": qty,
                                "submitted": True
                            })
                            save_player(player)
                            st.success("提出が完了しました。結果が出るまでお待ちください。")


# --- 管理者画面 ---
def show_admin_ui(class_name):
    st.header("🔐 管理者モード")
    group_value = load_group_value()
    round_num = load_round()
    confirmed = load_confirmation()
    players = load_all_players()
    submitted_players = [p for p in players if p["submitted"]]
    conn = connect()
    c = conn.cursor()
    st.subheader("現在の状況")
    st.markdown(f"**クラス名｜{class_name}**")
    st.markdown(f"**ラウンド {round_num}｜グループ価値:** {group_value} 円")
    st.markdown(f"**参加人数:** {len(players)} 人")
    st.markdown(f"**提出済み人数:** {len(submitted_players)} 人")
    st.subheader("📊 プレイヤーデータ")
    df = pd.read_sql_query("SELECT * FROM players", conn)
    st.dataframe(df)
    # 逆需要・供給関数グラフ描画
    players = load_all_players()
    price_range = list(range(0, 201))
    prices, demand, supply = compute_inverse_demand_supply(players)

    fig, ax = plt.subplots()
    ax.plot(demand, prices, label="D")
    ax.plot(supply, prices, label="S")
    ax.set_xlabel("Q")
    ax.set_ylabel("P")
    ax.set_title("Market Demmand and Supply")
    ax.legend()
    st.pyplot(fig)

    st.subheader("📈 市場精算処理")
    if st.button("価格を集計して表示"):
        price = set_payoffs(submitted_players, group_value, class_name, round_num)
        st.success(f"市場価格は {price} 円に設定されました。")

   
    if not confirmed:
        if st.button("報酬を確定する"):
            confirm_results()
            st.success("結果を確定しました。プレイヤーに報酬が表示されます。")

    if confirmed:
        st.subheader("結果一覧（報酬確定済）")
        players = load_all_players()
        df_result = pd.DataFrame(players)
        st.dataframe(df_result[["name", "choice", "bid", "qty", "money", "endowment", "payoff"]], use_container_width=True)

    st.subheader("📦 履歴ダウンロード")
    conn = connect()
    history_df = pd.read_sql_query("SELECT * FROM player_history WHERE class_name = ?", conn, params=(class_name,))
    conn.close()
    history_csv = history_df.to_csv(index=False).encode("utf-8")
    st.download_button("履歴CSVをダウンロード", data=history_csv, file_name=f"history_{class_name}.csv", mime="text/csv")


    # ラウンド制御
    st.sidebar.header("実験制御")
    if st.sidebar.button("次のラウンドへ"):
        next_round()
        st.sidebar.success("ラウンドを進めました")

    if st.sidebar.button("実験リセット"):
        reset_experiment()
        st.sidebar.success("全てのプレイヤーデータをリセットしました")
        time.sleep(2)
        st.rerun()
    st.sidebar.header("更新")
    if st.sidebar.button("更新"):
        st.rerun()


# --- メイン処理 ---
def main():
    query_params = st.query_params
    class_name = query_params.get("class", "Unknown")
    mode = query_params.get("mode", "player")
    initialize_db()

    if class_name == "Unknown":
        st.error("クラス情報がURLに含まれていません。例: ?class=A")
        return

    with st.sidebar:
        st.title("⚙️ モード切替")
        admin_mode = st.checkbox("管理者モード")

    if admin_mode:
        password = st.sidebar.text_input("パスワード", type="password")
        if password == st.secrets["admin_pw"]:
            show_admin_ui(class_name)
        else:
            st.error("パスワードが違います")
            st.stop()
    else:
        show_player_ui(class_name)

if __name__ == "__main__":
    main()

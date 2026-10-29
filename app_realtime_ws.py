import streamlit as st
import threading
import asyncio
import websockets
import json
import time
import pandas as pd

# 1. Cấu hình trang Streamlit
st.set_page_config(page_title="Bảng Giá WebSocket Realtime", layout="wide")
st.title("⚡ Bảng Giá Chứng Khoán Realtime qua WebSocket")

# 2. Khởi tạo bộ nhớ chia sẻ toàn cục (Shared State trong session_state)
if "live_data" not in st.session_state:
    st.session_state.live_data = {
        "TCB": {"price": 34.2, "change": "+0.4", "volume": 150000},
        "SSI": {"price": 20.3, "change": "+0.5", "volume": 230000},
        "HPG": {"price": 21.35, "change": "+0.25", "volume": 500000},
    }
if "ws_status" not in st.session_state:
    st.session_state.ws_status = "🔴 Đang ngắt kết nối"

# 3. Hàm chạy WebSocket ngầm trong Background Thread
def run_websocket_worker():
    """Hàm chạy vòng lặp asyncio bất đồng bộ trong một thread riêng biệt"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def socket_listener():
        # URL WebSocket giả định của nguồn dữ liệu (Ví dụ endpoint của TCBS/SSI hoặc Local Gateway)
        # Lưu ý: Một số CTCK yêu cầu token xác thực hoặc cơ chế handshake riêng.
        WS_URL = "wss://echo.websocket.events" # Dùng server test công khai để demo

        while True:
            try:
                st.session_state.ws_status = "🟡 Đang kết nối..."
                async with websockets.connect(WS_URL) as websocket:
                    st.session_state.ws_status = "🟢 Đã kết nối Realtime"

                    # Gửi đăng ký mã cổ phiếu nếu cần (Ví dụ: sub room TCB, SSI)
                    await websocket.send(json.dumps({"action": "sub", "symbols": ["TCB", "SSI", "HPG"]}))

                    async for message in websocket:
                        data = json.loads(message)
                        # Giả lập dữ liệu trả về từ WS cập nhật vào st.session_state
                        # (Thực tế bạn sẽ parse JSON dữ liệu khớp lệnh tick-by-tick từ sàn)
                        timestamp = time.time()

                        # Cập nhật giả lập biến động giá nhẹ để thấy hiệu ứng realtime
                        for code in st.session_state.live_data:
                            st.session_state.live_data[code]["volume"] += int(timestamp % 10) * 100

            except Exception as e:
                st.session_state.ws_status = f"🔴 Mất kết nối, đang thử lại... ({e})"
                await asyncio.sleep(5) # Chờ 5 giây trước khi kết nối lại

    loop.run_until_complete(socket_listener())

# 4. Tự động khởi chạy Background Thread (Chỉ chạy 1 lần duy nhất khi app khởi động)
@st.cache_resource
def start_background_thread():
    t = threading.Thread(target=run_websocket_worker, daemon=True)
    t.start()
    return t

start_background_thread()

# 5. Khu vực hiển thị giao diện Streamlit (Tự động làm mới mỗi 2 giây)
st.sidebar.subheader("Trạng thái kết nối")
st.sidebar.write(st.session_state.ws_status)

# Dùng Fragment để tự động cập nhật bảng mà không làm load lại toàn trang
@st.fragment(run_every=2)
def render_dashboard():
    st.subheader(f"⏱️ Dữ liệu cập nhật lúc: {time.strftime('%H:%M:%S')}")

    # Chuyển đổi dict dữ liệu sang DataFrame để hiển thị bảng đẹp mắt
    df = pd.DataFrame.from_dict(st.session_state.live_data, orient='index')
    df.reset_index(inplace=True)
    df.rename(columns={'index': 'Mã CP', 'price': 'Giá', 'change': '+/-', 'volume': 'Khối lượng'}, inplace=True)

    # Hiển thị bảng dạng lưới
    st.dataframe(df, use_container_width=True, hide_index=True)

render_dashboard()
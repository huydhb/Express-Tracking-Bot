# 📦 Express Tracking Bot

Bot Telegram theo dõi đơn hàng SPX Express (Trma vận đơn) - Tự động cập nhật trạng thái giao hàng theo thời gian thực.

## 🎯 Tính năng

- ✅ **Theo dõi đơn hàng**: Thêm/quản lý các mã vận đơn SPX
- 🔔 **Thông báo tự động**: Cập nhật trạng thái giao hàng định kỳ
- 📝 **Thông tin chi tiết**: Hiển thị chi tiết từng giai đoạn vận chuyển
- 📊 **Timeline**: Xem lịch sử vận chuyển của đơn hàng
- 🏷️ **Tên gợi nhớ**: Đặt tên cho từng đơn để dễ quản lý
- 🔄 **Refresh nhanh**: Cập nhật trạng thái đơn hàng trên yêu cầu
- ⏱️ **Tuỳ chỉnh khoảng thời gian**: Điều chỉnh tần suất kiểm tra (1-60 phút)

## 📋 Yêu cầu hệ thống

- Python 3.8+
- Telegram Bot Token (từ BotFather trên Telegram)
- Kết nối Internet

## 🚀 Cài đặt

### 1. Clone dự án

```bash
git clone https://github.com/yourusername/Express-Tracking-Bot.git
cd Express-Tracking-Bot
```

### 2. Tạo môi trường ảo (Optional nhưng khuyến khích)

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 3. Cài đặt dependencies

```bash
pip install -r requirements.txt
```

### 4. Cấu hình biến môi trường

Tạo file `.env` trong thư mục gốc:

```env
BOT_TOKEN=your_telegram_bot_token_here
POLL_MINUTES=5
```

- `BOT_TOKEN`: Token của bot Telegram (bắt buộc)
- `POLL_MINUTES`: Khoảng thời gian kiểm tra trạng thái (mặc định: 5 phút)

### 5. Chạy bot

```bash
python main.py
```

Bot sẽ bắt đầu chạy và hiển thị: "Bot đang chạy!"

## 💬 Cách sử dụng

### Lệnh chính

| Lệnh                              | Mô tả                                  |
| --------------------------------- | -------------------------------------- |
| `/start`                          | Bắt đầu bot và hiển thị hướng dẫn      |
| `/help`                           | Hiển thị danh sách tất cả các lệnh     |
| `/track <mã_vận_đơn>`             | Kiểm tra trạng thái ngay một đơn hàng  |
| `/add <mã_vận_đơn> [tên_gợi_nhớ]` | Thêm đơn hàng vào danh sách theo dõi   |
| `/list`                           | Hiển thị tất cả đơn hàng đang theo dõi |
| `/remove <mã_vận_đơn>`            | Xoá đơn hàng khỏi danh sách            |
| `/interval [phút]`                | Xem/thay đổi khoảng thời gian kiểm tra |
| `/stop`                           | Dừng bot                               |

### Ví dụ sử dụng

```
/add SPXVN123456 Mua sắm online
/track SPXVN123456
/interval 10
/list
/remove SPXVN123456
```

## 🔧 Cấu trúc dự án

```
Express-Tracking-Bot/
├── main.py              # File chính của bot
├── requirements.txt     # Dependencies
├── .env                 # Biến môi trường (không commit)
├── .env.example        # Template cho .env
├── render.yaml         # Config để deploy trên Render
└── README.md           # File này
```

## 📦 Dependencies

- `python-telegram-bot[job-queue]==22.5` - Thư viện Telegram Bot
- `requests==2.32.3` - HTTP client
- `python-dotenv==1.0.1` - Quản lý biến môi trường
- `Flask==3.0.3` - Web framework để health check

## 🌐 Deployment

### Trên Render

1. Tạo tài khoản trên [render.com](https://render.com)
2. Connect GitHub repository
3. Tạo Worker mới với cấu hình:
   - **Environment**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Environment Variables**:
     - `BOT_TOKEN`: Token của bot Telegram

File `render.yaml` đã được cấu hình sẵn cho deployment.

### Chạy cục bộ (Local)

```bash
# Cài đặt environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Cài dependencies
pip install -r requirements.txt

# Tạo .env file
echo "BOT_TOKEN=your_token_here" > .env
echo "POLL_MINUTES=5" >> .env

# Chạy
python main.py
```

## 📍 API được sử dụng

Bot sử dụng API của Trma vận đơn:

- **Endpoint**: `https://tramavandon.com/api/spx.php`
- **Method**: POST
- **Payload**: `{"tracking_id": "SPXVN..."}`

## 🗂️ Cơ sở dữ liệu (Persistence)

Dữ liệu được lưu tự động bằng PicklePersistence:

- Danh sách đơn hàng theo dõi
- Tên gợi nhớ của từng đơn
- Khoảng thời gian kiểm tra
- Thông tin ghi nhận cuối cùng

## ⚙️ Tuỳ chỉnh

### Thay đổi múi giờ

Mở `main.py` và sửa:

```python
TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")
```

### Thay đổi User-Agent

Để tránh bị block, có thể sửa User-Agent trong API_HEADERS

### Cache TTL

Chỉnh thời gian cache để tránh spam API:

```python
CACHE_TTL_SECONDS = 20  # 20 giây
```

## 🐛 Troubleshooting

| Lỗi                           | Giải pháp                                       |
| ----------------------------- | ----------------------------------------------- |
| "Thiếu BOT_TOKEN trong .env"  | Kiểm tra file `.env` có `BOT_TOKEN` không       |
| Bot không phản hồi            | Kiểm tra lại BOT_TOKEN có đúng không            |
| API lỗi "Invalid tracking_id" | Mã vận đơn phải có định dạng `SPXVN...`         |
| Timeout khi gọi API           | Kiểm tra kết nối Internet, hoặc API có thể down |

## 📝 Ghi chú

- Bot sẽ tự động lưu trạng thái (persist) cho mỗi chat
- Mỗi chat sẽ có một job polling riêng
- Khoảng thời gian mặc định là 5 phút, có thể tuỳ chỉnh
- Dữ liệu tracking được cache 20 giây để tránh spam API

## 🤝 Đóng góp

Hãy gửi pull request hoặc báo cáo lỗi qua Issues!

## 📄 Giấy phép

MIT License - Tự do sử dụng và phân phối

## 👨‍💻 Tác giả

**Bảo Huy** - [GitHub](https://github.com/huydhb)

---

⭐ Nếu thích dự án này, hãy cho một sao ⭐

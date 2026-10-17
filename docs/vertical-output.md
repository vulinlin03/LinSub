# Khung hình dọc 9:16

Manual: dùng dropdown khung hình trên toolbar editor. Auto và Batch:
mở **Cài đặt xử lý → Hình ảnh và âm thanh → Khung hình đầu ra**.
Đây là cài đặt đầu ra được lưu theo dự án, không phải mức zoom của preview.
Batch lưu lựa chọn chung và vẫn cho phép từng video có cài đặt riêng.

- **Tỷ lệ gốc** (mặc định): không thay đổi khung hình.
- **9:16 Nền mờ**: xuất 1080 × 1920, giữ trọn video chính ở giữa.
  Khoảng trống trên/dưới (hoặc hai bên với nguồn rất hẹp) dùng bản sao
  chuyển động của video, phóng đầy khung và làm mờ. Không nhân đôi âm thanh.
- **9:16 Đầy khung**: phóng đầy khung dọc; nội dung ở ngoài có thể bị cắt.

Phụ đề và watermark được đặt trên khung đầu ra. Vùng OCR/che phụ đề vẫn
được xử lý theo tọa độ nguồn trước khi ghép nền, không chạy lại nhận dạng,
dịch hay tạo giọng chỉ vì đổi tỷ lệ. Manual vẫn có thể chỉnh lại vị trí
phụ đề/watermark nếu bố cục cũ không phù hợp khung dọc.

Preview Manual ghép khung dọc trực tiếp trên video/cache nguồn đã xử lý,
không render lại toàn bộ video khi đổi tỷ lệ. Nền mờ lấy cùng khung hình
từ một decoder, làm mờ texture nhỏ tối đa 270 × 480; khi pause, texture
chỉ cập nhật khi có khung hình mới. Cache OCR, âm thanh và các kết quả AI
không bị xóa. Nếu chưa có proxy cho một thay đổi thực sự như vùng OCR,
crop hay cắt nguồn, proxy đó vẫn cần được tạo và có thanh tiến độ.
Auto dùng đường preview/render đầu ra hiện có. Xuất vẫn dùng FFmpeg,
không phụ thuộc khả năng xử lý shader của preview.
Xuất một đoạn vẫn chỉ đọc/render phạm vi đoạn đã chọn.

Nền được làm mờ ở độ phân giải 270 × 480 rồi phóng lên; foreground
được scale trực tiếp từ nguồn, không qua nhánh giảm độ phân giải này.
Preset chất lượng xuất có thể giảm/tăng kích thước cuối, nhưng giữ tỷ lệ 9:16.
Video đã đúng 9:16 giữ nguyên kích thước nguồn, bỏ qua phóng/cắt/thêm nền
9:16. Quyết định này được tính độc lập cho từng video trong Batch, không
đổi cài đặt chung hay cài đặt video kế tiếp. Các bước xử lý được người dùng
chọn (phụ đề, giọng đọc, lớp che, watermark) vẫn hoạt động bình thường.
Nếu người dùng chủ động crop nguồn thành tỷ lệ khác, khung đầu ra được
đánh giá theo vùng crop đó.

9:16 chỉ chuẩn hóa hình ảnh; không tự đảm bảo đủ điều kiện đăng Shorts,
TikTok hay Reels (còn thời lượng, âm nhạc/bản quyền và giới hạn nền tảng).

Tham khảo:
- [CapCut: resize và nền mờ](https://www.capcut.com/tools/video-resizer)
- [FFmpeg: scale, split, boxblur và overlay](https://ffmpeg.org/ffmpeg-filters.html)
- [YouTube: điều kiện video Shorts](https://support.google.com/youtube/answer/15424877?hl=en)

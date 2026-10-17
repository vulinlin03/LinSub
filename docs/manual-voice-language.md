# Ngôn ngữ phụ đề và công cụ giọng đọc

Manual phân biệt `target_language` (lựa chọn cho lần dịch tiếp theo) và
`subtitle_language` (ngôn ngữ của kết quả phụ đề đã xuất bản thành công).
Đổi ô Dịch sang không tự đổi công cụ giọng của các phụ đề đang có.
Các dự án cũ chưa có trường mới được kiểm tra nội dung phụ đề khi cần.

VieNeu vẫn cho phép phụ đề Việt/Anh; không cam kết chất lượng tiếng Anh
bằng tiếng Việt. Nếu kết quả mới là Trung và đang dùng VieNeu, Manual
chuyển sang OmniVoice cùng CPU/GPU, bỏ override VieNeu không tương thích.
Việc chuyển chỉ xảy ra sau khi dịch thành công, không khi đổi ô ngôn ngữ
hay khi dịch thất bại. Chọn lại VieNeu với phụ đề Trung bị chặn và cảnh báo
mỗi lần thử, không dùng cơ chế thông báo một lần mỗi phiên.

Auto/Batch kiểm tra ngôn ngữ đích trước khi chạy. Hai chiều chọn Trung
khi dùng VieNeu hoặc chọn VieNeu khi đích là Trung đều bị chặn; lựa chọn
hợp lệ trước đó được giữ lại. Backend cũng kiểm tra trước khi tạo giọng.

Xuất SRT bị khóa khi đang xử lý, có tác vụ trong hàng đợi, đang xuất video
hoặc nhập nguồn. Hàm xuất kiểm tra trước khi đọc tài liệu và mở hộp chọn tệp.

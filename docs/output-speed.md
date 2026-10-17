# Tốc độ video đầu ra

Manual: nút **Tốc độ 1×** cạnh nút Xuất trên toolbar mở thanh kéo tốc độ.
Auto/Batch: nút **1×** cạnh Khung hình đầu ra trong nhóm Hình ảnh và âm thanh.
Kéo thanh từ 0,25–3×, bước 0,05×; mặc định 1×. Popup chỉ giữ thanh kéo
và số tốc độ, không có các nút mốc hoặc đoạn giải thích bên dưới.

Thiết lập này độc lập với tốc độ nghe preview. Timeline vẫn dùng thời gian
biên tập gốc. Khi xuất, hình ảnh đã ghép phụ đề/watermark/lớp phủ và toàn bộ
âm thanh được đổi tốc độ cùng nhau. FFmpeg `atempo` giữ cao độ giọng đọc.
Xuất một đoạn chỉ đổi tốc độ đoạn đã chọn; không xử lý phần video ngoài đoạn.
Thời lượng đầu ra bằng thời lượng đoạn/tốc độ.

Tốc độ chỉ tham gia cache xuất. OCR, phụ đề, mapping người nói, từng câu TTS,
audio mix và proxy biên tập không đổi. Mức 1× giữ chữ ký cache xuất cũ.
Khi chọn chất lượng có resize, đổi tốc độ được ghép vào cùng lượt encode cuối.

Manual lưu riêng tốc độ xuất, không làm mới preview, đọc lại phụ đề/giọng hay
phát tín hiệu tải lại dự án. Undo/redo tốc độ cũng dùng đường lưu riêng này.
Nút tốc độ trong hàng điều khiển preview mở thanh kéo ngang 0,25–3×;
hiện số trong lúc kéo và chỉ áp dụng tốc độ nghe khi thả chuột. Hai tốc độ
preview/đầu ra vẫn hoàn toàn độc lập.

Preview âm thanh trộn dùng cùng chuỗi FFmpeg `atempo` như xuất, không còn
resample đổi cao độ. PCM được trộn theo khối 50 ms ở luồng nền; hàng đợi đầu
ra tối đa 8 khối, không render toàn video hay tạo cache tốc độ mới. Seek,
pause, đổi tốc độ, đổi âm lượng/clip, chuyển dự án và đóng app hủy bộ lọc cũ.
Nguồn phát trực tiếp bằng Qt bật `pitchCompensation`. Mức 1× giữ mixer PCM cũ.

Đối chiếu read-only `fff` ngày 14/10/2026: mẫu giọng đầu tiên tại 2× qua luồng
preview mới khớp từng mẫu PCM với bộ lọc xuất `atempo=2` (48.000 khung stereo).
Kiểm thử tín hiệu 220/440 Hz ở 0,25–3× giữ tần số, kiểm tra backpressure,
partial write và giải phóng process khi hủy. Đây là kiểm thử bộ lọc/âm thanh,
không thay dữ liệu hay chạy lại TTS của dự án.

# Giới hạn phát âm OmniVoice

Chẩn đoán work9 ngày 13/10/2026: các câu "Tôi không biết" và "Chúc ngủ ngon"
đã không được nhận dạng đầy đủ ngay từ MP3 do model tạo, trước trim/fit timeline.
File gốc dài 590/450 ms, phần sau trim còn 340/210 ms. Đây không phải bằng
chứng rằng mọi lỗi đều do thời lượng: tên riêng pha tiếng Anh cũng có thể
bị phát âm lệch trong chế độ Vietnamese. ASR chỉ là phép đối chiếu, không phải
kết luận tuyệt đối về nội dung nghe được.

Giữ fade 0, không sửa chữ/tên riêng trong phụ đề, không ép duration, mượn
câu tham chiếu hay thêm ASR/retry vào tiến trình sản xuất. Nhãn backend
hướng dẫn VieNeu cho tiếng Việt và OmniVoice cho tiếng Anh/Trung; không
ngụ ý OmniVoice hoàn toàn không hỗ trợ tiếng Việt.

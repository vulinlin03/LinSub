# LinSub 1.0.1

[Tải bộ cài Windows](https://github.com/vulinlin03/LinSub/releases/download/v1.0.1/LinSub-1.0.1-Setup.exe)

## Sửa lỗi và cải thiện

- Nhận dạng & dịch lưu tiến độ theo từng giai đoạn. Tiếp tục dùng lại phần nhận dạng/căn thời gian đã hoàn thành, giữ các batch dịch đã nhận và không bỏ kết quả Gemini trả về đúng lúc tạm dừng. Giai đoạn chưa hoàn thành vẫn có thể cần nạp lại model; yêu cầu mạng chưa nhận kết quả có thể phải gửi lại.
- Sửa các ranh giới Tạm dừng/Tiếp tục/Hủy/Chạy lại: không tự chạy tiếp sau khi đã dừng, không dùng nhầm cache cũ sau khi chạy lại hoặc đổi nguồn, giữ kết quả hoàn thành khi tạm dừng và chỉ dọn kết quả của lượt bị hủy. Giữ kết quả có từ trước.
- Sửa lỗi xác minh cache tách giọng trên một số đường dẫn Windows dài hoặc có ký tự Unicode, đồng thời giữ kiểm tra tính toàn vẹn cache.
- Sửa trường hợp nhận dạng/dịch gặp lỗi rồi chạy lại bị báo khóa tệp; cải thiện khôi phục trạng thái và giải phóng tài nguyên.
- Tách luồng nhập/đăng mạng xã hội khỏi thao tác mở và tạo dự án không liên quan.
- Thêm Chỉnh vùng che trong Auto và Hàng loạt: bật/tắt Tự động nhận diện, chỉnh vùng trên video gốc và lưu cấu hình. Preview chỉ hiển thị vùng; làm mờ/vá nền được áp dụng khi xử lý video.
- Cải thiện phản hồi khi đổi các lựa chọn Auto, giữ trạng thái vị trí preview.
- Cải thiện tải Bilibili khi kết nối bị ngắt giữa chừng và ưu tiên nhập luồng tải được mà không encode lại không cần thiết.
- Cải thiện dừng/hủy tải, bỏ qua callback đến muộn, dọn tệp tải dở an toàn và sao chép khác ổ đĩa có thể hủy.
- Phụ đề karaoke kết thúc hiển thị khi phần giọng/chữ đã hoàn thành thay vì giữ dư đến hết timestamp.
- Thông báo rõ khi TikTok giới hạn quyền xem và yêu cầu đăng nhập; không coi đây là lỗi mạng để thử lại liên tục, không tự truy cập cookie trình duyệt.
- Giữ cache SRT/render hợp lệ khi tiếp tục, tránh tạo lại kết quả chỉ do ghi lại cùng nội dung SRT.

## Cập nhật từ 1.0.0

Mở **Phiên bản mới → Cập nhật** để nâng trực tiếp từ **1.0.0**. Bản phát hành có Delta **1.0.0 → 1.0.1**, Core đầy đủ dự phòng và bộ cài mới. Dữ liệu dự án, thiết lập và model được giữ nguyên; các release cũ không bị xóa.

Sau cập nhật, mở **Gói tài nguyên** và cập nhật **bộ xử lý CPU/CUDA** khi app yêu cầu. Các sửa lưu điểm nhận dạng và xử lý nằm cả trong bộ xử lý mới, không chỉ Core. Gói OCR/model không cần tải lại nếu vẫn hợp lệ. Điểm lưu chi tiết mới được tạo bởi lượt chạy dùng bản sửa; không thể khôi phục bước mà phiên bản cũ chưa từng lưu.

Các thư viện và codec không thay đổi so với 1.0.0; [nguồn thư viện tương ứng](https://github.com/vulinlin03/LinSub/releases/download/v1.0.0/LinSub-1.0.0-ThirdPartySources.zip) tiếp tục được cung cấp ở release 1.0.0.

Windows x64. Bản Windows **chưa ký Authenticode**; Windows có thể cảnh báo hoặc chặn theo chính sách. Không tắt phần mềm bảo vệ. Kiểm thử cục bộ/frozen không thay thế mọi cấu hình Windows hoặc điều kiện mạng thực tế. Các giới hạn giấy phép model, bao gồm checkpoint OmniVoice phi thương mại, vẫn áp dụng.

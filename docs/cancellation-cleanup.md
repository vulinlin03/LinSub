# Hủy tác vụ và dọn file tạm

- Lượt xuất ghi token của từng lần enqueue mà nó tạo. Hủy chỉ gỡ các video
  đang chờ thuộc lượt đó và cancel đúng tác vụ active có cùng token. Không dựa
  vào dự án/video đang hiển thị; token cũ không thể dừng lần chạy mới.
- Giữ nguyên UI Tạm dừng → Tiếp tục / Chạy lại. Tạm dừng giữ các phần đã tạo;
  Tiếp tục dùng cùng lượt; Chạy lại bỏ tiến độ của lượt tạm dừng/thất bại rồi
  tạo lượt mới. Auto/Batch restart giữ cơ chế reset generated workspace.
  Generated workspace được đổi tên sang vùng riêng trước khi reset metadata;
  nếu đổi tên/ghi metadata lỗi thì khôi phục. Chỉ dọn vùng đã tách sau commit,
  nên file bị khóa không để lại cache cũ bị xóa một nửa ở đường dẫn chính.
- Hủy xuất dọn cả các kết quả hoàn thành do lượt bị hủy tạo ra, không chỉ file
  dở. Journal `processing-run.json` ghi mốc trước lượt và các artifact được
  publisher của chính worker tạo; không xóa cache đã có từ trước, source,
  nhạc/watermark nhập vào hay file xuất ngoài app đã hoàn tất.
- Cache nền trước lượt được bảo vệ khỏi GC trong lúc xử lý. Metadata chỉ
  khôi phục các tham chiếu của lượt đó; không đụng artifact do editor khác tạo.
  Auto có temp file mutable được sao lưu ở worker trước lượt xuất cancellable;
  Manual dùng artifact immutable nên không sao chép media dài trên GUI.
- Đang hủy dựng vẫn tính là export busy đến khi producer chạy xong `finally`
  và dọn xong phần bị khóa/pin. Có khóa thì giữ journal và thử lại; không
  bắt đầu một lượt mới trên phần workspace chưa dọn xong.
- Mỗi lần tải kênh dùng thư mục UUID riêng. Hủy/lỗi tải, lỗi import và callback
  đến sau lệnh hủy đều dọn đúng thư mục đó; không đụng thư mục lần tải mới.
- File xuất dở, thư mục xuất đoạn và thư mục tải bị khóa tạm thời được ghi vào
  `pending-temporary-cleanup.json`, thử dọn lại ở nền và khi mở app sau đó.
  Chỉ xử lý đường dẫn đã ghi nhận; không quét/xóa các cache khác. Kiểm tra
  identity, containment, symlink/junction và project lồng trước khi dọn.
- Không thể xóa ngay khi Windows vẫn giữ khóa hoặc ổ đĩa bị ngắt. File dở
  không được coi là kết quả/cache hợp lệ; dọn lại khi truy cập được.
- Dừng tải gói tài nguyên vẫn giữ phần tải để resume theo cơ chế hiện có;
  thao tác bỏ lượt tải riêng mới xóa phần tải đó.

Kiểm thử: hủy FFmpeg thật khi dựng, hủy giữa lúc copy, callback tải kênh muộn,
chạy lại cùng video, chuyển dự án, giữ file xuất/cache hoàn thành, journal
khởi động lại và khóa file thật bằng Windows `CreateFileW` không chia sẻ.

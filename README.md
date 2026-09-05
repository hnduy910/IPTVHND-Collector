# IPTVHND Collector

Collector lưu trữ IPTV theo kiểu **append-only** và xuất ra `iptvhnd.m3u`.

## Quy tắc

- Chạy tự động mỗi 12 giờ bằng GitHub Actions.
- Việt Nam: giữ toàn bộ entry từ các nguồn Việt Nam cấu hình sẵn.
- Quốc tế: giữ playlist phim và thể thao/bóng đá từ các nguồn cấu hình sẵn.
- Không health-check; link chết vẫn được lưu.
- Không xóa link cũ khi nguồn upstream xóa link đó.
- Chỉ chống trùng theo **URL stream**.
- Cùng tên kênh nhưng URL khác nhau: giữ tất cả.
- Giữ nguyên `#EXTINF`, `tvg-id`, `tvg-name`, `tvg-logo`, `group-title`, catchup và các directive đi kèm entry như `#EXTVLCOPT` khi nguồn có cung cấp.

## Nguồn

Sửa `config/sources.txt`, mỗi dòng một URL playlist M3U. Bản mẫu dùng các playlist công khai của iptv-org cho Việt Nam, Movies và Sports.

## Chạy thủ công

```bash
python collector/main.py --sources config/sources.txt --output iptvhnd.m3u
```

## GitHub Actions

Workflow `.github/workflows/collect.yml` chạy tại phút 17 mỗi 12 giờ (UTC) và tự commit nếu có URL mới.

> Chỉ thêm các nguồn công khai hoặc nguồn bạn có quyền sử dụng. Collector không có chức năng vượt DRM, token protection, paywall hay cơ chế bảo vệ truy cập.

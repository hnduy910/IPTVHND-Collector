# IPTVHND Collector

IPTVHND-Collector duy trì một kho URL IPTV công khai/community dành cho **truyền hình/video**, sau đó tạo các playlist Live đã kiểm tra. Collector không phải là nguồn IPTV trả phí và không cố truy cập luồng được bảo vệ.

## Các output

- `iptvhnd.m3u` — Archive TV/video tích lũy. URL TV/video không bị xóa chỉ vì health-check thất bại hoặc upstream xóa URL. Exact stream URL được chống trùng; cùng tên kênh nhưng URL khác nhau vẫn được giữ. Radio/audio-only chỉ bị loại khi metadata có độ tin cậy cao hoặc manifest chứng minh đó là audio-only.
- `iptvhnd-vietnam-live.m3u` — toàn bộ kênh truyền hình Việt Nam đang sống từ Archive, không lọc theo resolution.
- `iptvhnd-football-live.m3u` — kênh/competition bóng đá mục tiêu đang sống, chỉ nhận video có variant thực tế từ `1920x1080` trở lên.
- `iptvhnd-movies-live.m3u` — kênh phim thuộc channel reference được chọn lọc, đang sống và từ `1920x1080` trở lên.
- `iptvhnd-live.m3u` — union tương thích ngược của ba Live playlist trên; output chính để tích hợp mới là ba file riêng.
- `stats.json` — thống kê theo Sources, Archive, Vietnam, Football, Movies và health của từng Live playlist.

Luồng dữ liệu là:

`public/community sources → discovery → loại radio/audio-only → phân loại → exact URL dedupe → Archive → health-check → 3 Live playlists`

Mọi URL trong ba Live playlist đều phải tồn tại trong Archive. Một HTTP 200 chứa HTML/error page không được coi là stream sống; HLS phải có cấu trúc playlist hợp lệ. Master HLS/DASH được đọc để xác minh video và resolution khi nhóm Football/Movies yêu cầu.

## Phân loại

Việt Nam được tìm rộng theo VTV, HTV/HTVC, THVL, VTC, SCTV, VTVcab/ON, QPVN, ANTV và đài địa phương. Danh sách công khai của MyTV, FPT Play, SCTV, VTVcab/ON trong `config/channel_reference.json` chỉ là reference để mở rộng mục tiêu discovery, không phải whitelist loại các đài khác và không phải nguồn stream trả phí.

Football chỉ nhận tín hiệu bóng đá/competition mục tiêu như Champions League, Europa League, Conference League, Premier League, FA Cup, Carabao/EFL Cup, Serie A, La Liga, Bundesliga, Ligue 1, Copa Libertadores và Copa Sudamericana. Sports chung không tự động được xem là football.

Movies dùng alias/channel family chọn lọc như HBO, Cinemax, Warner TV, Box Movies, ON Movies, ON Phim Việt, ON Cine, ON Vie Dramas, Star Movies và một số FAST/cinema channel tương đương. Từ `movie`, `film` hoặc `cinema` một mình không đủ để nhận. Netflix protected streams, DRM, token, credentials, cookie, private API và paywall luôn bị loại; project không bypass DRM/access control.

Radio, FM, AM, podcast, audio-only HLS và audio-only MPEG/MP3/AAC không được đưa vào Archive hoặc bất kỳ Live playlist nào. Từ `music` một mình không khiến một kênh music TV bị loại.

## Nguồn và chạy thủ công

`config/sources.txt` là registry tự duy trì, không phải danh sách đóng. Discovery luân phiên tìm file `.m3u`, `.m3u8`, `.txt` và tài liệu công khai có chứa playlist IPTV hợp lệ. Source đang có chỉ bị xóa sau ba lần kiểm tra liên tiếp thất bại; lỗi tạm thời không xóa source.

```bash
python collector/discover_sources.py \
  --sources config/sources.txt \
  --state config/source_state.json

python collector/main.py \
  --sources config/sources.txt \
  --source-state config/source_state.json \
  --output iptvhnd.m3u \
  --vietnam-live-output iptvhnd-vietnam-live.m3u \
  --football-live-output iptvhnd-football-live.m3u \
  --movies-live-output iptvhnd-movies-live.m3u \
  --legacy-live-output iptvhnd-live.m3u \
  --stats stats.json

python collector/validate_outputs.py
python -m pytest -q
```

`--skip-health` chỉ dành cho fixture/offline run; output phát hành phải đi qua health-check thực tế. Workflow GitHub Actions chạy tests, discovery, collector, validation và commit mỗi 12 giờ. Workflow có concurrency, push an toàn sau rebase và commit dữ liệu dùng `[skip ci]` để tránh vòng lặp bot → workflow → bot.

Chỉ thêm nguồn công khai/community mà bạn có quyền sử dụng. Collector không bypass DRM, token protection, paywall, credentials, cookie hoặc cơ chế access control riêng tư.

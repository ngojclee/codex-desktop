# Codex Desktop — BÀN GIAO CHO DEV (Dev Handoff)

> Mục tiêu của dự án: tự động "repatch" bản Codex Desktop chính thức (OpenAI phát hành) thành bản `-patched`
> mỗi khi có bản mới, qua CI GitHub Actions, kèm theo catalog model tuỳ biến và config CPA.
> Tài liệu này tổng hợp mọi lỗi đã gặp, cách fix, và các điểm dev **PHẢI** sửa lại cho đúng.

---

## 1. TỔNG QUAN KIẾN TRÚC

- **Repo:** `ngojclee/codex-desktop` (fork từ OpenAI codex-desktop).
- **CI:** `.github/workflows/auto-repatch-release.yml`
  - Trigger: phát hiện bản mới (tag/version) → tải bản chính thức → chạy 18 patch scripts → build → tạo GitHub Release `v<ver>-patched`.
  - Step "Send status email" chạy `if: always()` (báo cả success lẫn failure) NHƯNG **đang disabled** vì chưa có secrets SMTP.
- **2 máy chạy app:** `10.11.1.1` (user) và `10.11.1.3` (pcfr-des-01-lan). Catalog/model json + config.toml phải **sync 2 máy**.
- **CPA (proxy model):** `http://10.21.1.101:8317/v1` (key nằm trong `config.toml`, đọc bằng regex Python, không hardcode).

---

## 2. DANH SÁCH 18 PATCHES (A–U)

`apply-all-patches.ps1` gọi tuần tự A→U. Trạng thái verify từ CI log:

| Script | Mục đích | Behavior |
|---|---|---|
| A | ... | patched |
| B | Electron fuse (disable) | **Skipped** với Owl Electron (Owl không dùng fuse) — đúng |
| C | ... v3 | patched |
| D | reconnect/clear conversation | **patched** (xem §3.1) |
| G–M | ... | patched |
| H | ... | patched |
| J | ... | patched |
| K | ... | patched |
| L | ... | already_patched |
| O–U | ... | patched / upstream_safe |
| T | unbind Ctrl+Shift+V voice mode | **upstream_safe** (Windows đã tự fix ở 26.831) |
| U | double-paste | **vẫn patched** (bản gốc chưa fix) |
| **B2** | **rewrite embedded app.asar header hash trong exe** | **đã sửa đúng = SHA256 header JSON — xem §4** |

> Dev cần đọc nội dung từng `patches/patch_*.py` để biết chi tiết; bảng trên chỉ tóm tắt hành vi.

---

## 3. BA LỖI NGHIÊM TRỌNG ĐÃ TÌM RA

### 3.1 Patch D — regex pagination (ĐÃ FIX TRONG REPO)
- Upstream 26.831+ đổi hàm thành `markAllConversationsNeedResumeAfterReconnect(){this.pagination.cancelItemLoads(),this.threadStore.resetAfterReconnect();...}` (thêm prefix `pag_cancel.`).
- Regex cũ không khớp → CI báo `pattern_not_found` → fail 5 lần.
- **Fix:** thêm group optional `(?:pag_cancel\.)?` vào regex. Đã commit, CI xanh.

### 3.2 invalid transport `codex_app` (ĐÃ FIX QUA config.toml)
- App 26.831+ inject `[mcp_servers.codex_app]` vào plugin `.mcp.json`/`desktop-mcp.json` với trường `transport`/`type` **bị thiếu** → lỗi `invalid transport` → app không load được config.toml → thread không resume được.
- **KHÔNG sửa file plugin** (app ghi đè mỗi lần update → vô ích).
- **Fix đúng:** thêm block tường minh vào `C:\Users\ngocl\.codex\config.toml`:
  ```toml
  [mcp_servers.codex_app]
  command = 'cmd.exe'
  args = ['/d', '/s', '/c', 'call', 'C:/Users/ngocl/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3/scripts/launch_codex_app_tools_mcp.cmd', 'C:/Users/ngocl/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3/server.mjs']
  cwd = 'C:/Users/ngocl/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3'
  enabled = true
  ```
  (Luôn backup `config.toml` trước khi sửa.) Block này override cái inject lỗi. User xác nhận "cách này đúng là fix đc".

### 3.3 Patch B2 — Owl Electron embedded asar hash (XEM §4 — ĐÃ FIX ĐÚNG)

---

## 4. LỖI LỚN NHẤT — INTEGRITY CHECK FAIL TRÊN BẢN RELEASE CI (đã tìm ra thuật toán thật, đã sửa B2)

### 4.1 Triệu chứng
Mở app báo:
```
FATAL:third_party\electron\shell\common\asar\asar_util.cc:143]
Integrity check failed for asar archive (EXPECTED vs ACTUAL)
```
App không chạy được.

### 4.2 Phát hiện thực tế trên CẢ 2 MÁY (bản `v26.901.20858-patched` — ĐÃ có Patch B2 từ CI)
Mỗi lần launch, log báo một cặp hash `(expected, actual)`:

| Máy | exe embedded expected (lần 1) | app computed actual | file app.asar SHA256 |
|---|---|---|---|
| 10.11.1.1 | `5d404e81` | `53f5e962` | `11d282a8` |
| 10.11.1.3 | `8d98a573` | `8dc558b4` | `9e1fccc...` |

→ **Quan sát then chốt:** `app computed actual` (`53f5e962` / `8dc558b4`) **KHÔNG BẰNG** SHA256 của cả file app.asar (`11d282a8` / `9e1fccc`).

### 4.2b THUẬT TOÁN THẬT — ĐÃ VERIFY (2026-09-04)

> Sửa lại kết luận cũ: giả thuyết "app đọc header `integrity`" là **SAI**. Header asar của build này
> chỉ có duy nhất key `files`, **không có section `integrity` nào cả**.

Đo trực tiếp trên 10.11.1.1 (app đang chạy được, nên giá trị embed trong exe chắc chắn khớp cái app tính).
Thử nhiều ứng viên hash trên đúng file app.asar đang cài:

| Ứng viên | Giá trị | Khớp `actual`? |
|---|---|---|
| SHA256(cả file app.asar) | `11d282a8...` | KHÔNG |
| **SHA256(blob JSON header)** | `53f5e962...` | **CÓ, khớp chính xác** |
| SHA256(header pickle `[8:8+u1]`) | `fdeb6f7a...` | KHÔNG |
| SHA256(size fields + header pickle) | `890ef118...` | KHÔNG |
| SHA256(content sau header) | `53578b8f...` | KHÔNG |
| SHA256(json + content) | `9bb658ca...` | KHÔNG |
| SHA256 block 4MiB/1MiB (raw + hex) | các giá trị khác | KHÔNG |

Kết luận đúng:

```text
expected (embed trong exe)  ==  SHA256(header JSON của app.asar)  ==  actual (app tính)
```

Layout header đã verify (26.901 Owl):

```text
[0]  uint32 = 4            outer size-pickle payload size
[4]  uint32 = header_size  (2429204)
[8]  uint32 = inner pickle payload size (2429200)
[12] uint32 = json_len     (2429195)
[16] json_len byte header JSON   <- đúng phần này được hash
```

### 4.3 Tại sao Patch B2 cũ SAI
B2 cũ set exe `expected` = **SHA256 của cả file app.asar**, trong khi app so với **SHA256 header JSON** → luôn lệch → FATAL.

Có một hệ quả phụ quan trọng: patches A..U là sửa **cùng số byte** trong vùng content, nên header **không hề đổi**.
Tức `actual` là hằng số theo từng release, còn SHA256 cả file thì đổi mỗi lần patch. B2 cũ vì thế vừa sai vừa không ổn định.

**ĐÃ SỬA trong repo** — `compute_asar_header_sha256()`:
- Tính SHA256 của header JSON (đúng thuật toán app dùng).
- Guard `json_len` (phải > 0 và < 1 GiB) để CI **fail loudly** nếu upstream đổi layout asar, thay vì âm thầm ghi hash sai.
- Test trên bản copy của exe đang chạy tốt → báo `Already correct ... No-op`, tức idempotent và không phá build đang chạy.

### 4.4 Cách fix thủ công đã áp dụng (workaround, cả 2 máy đều chạy được)
Tìm blob JSON hash trong exe, thay `value` = chuỗi `actual` app báo:
```python
# tmp_scripts/fix_103_integrity_v3.py (trên 10.11.1.3)
# 1) đọc file app.asar SHA256 (chỉ để tham khảo)
# 2) tìm expected cũ trong exe, thay bằng reported_actual = b'8dc558b4...'
# 3) backup exe trước khi ghi
```
Sau khi vá: app mở OK (process chạy, `model/list`/`getAuthStatus` trả `errorCode=null`).

### 4.5 VIỆC CÒN LẠI CHO DEV
1. ~~Sửa Patch B2~~ **ĐÃ LÀM** — B2 tính SHA256 header JSON (xem §4.2b).
2. **Force rebuild CI** để bản `-patched` phát hành embed đúng hash; các máy khác cài là chạy, hết phải vá tay exe.
3. Thêm bước **smoke test launch trong CI**: chạy exe vừa build, nếu log ra `Integrity check failed` thì in cặp `(expected, actual)` và fail ngay tại CI.
4. Giữ B2 là bước **cuối** sau mọi patch làm đổi asar (đã đúng trong `apply-all-patches.ps1`) và không repack asar sau B2.

---

## 5. BẢO TRÌ MODEL CATALOG

File: `C:\Users\ngocl\.codex\model_catalog.json` + `models_cache.json` (phải sync 2 máy, scp qua `pcfr-des-01-lan`).

### Quy tắc vàng
- **`slug` = route CPA thật** (vd `tencent/hy4-preview`, `deepseek/deepseek-v4-flash`). KHÔNG có dấu cách, KHÔNG dùng mã `NR` trong slug.
- **`display_name`** = tuỳ ý, có thể mang mã `NR` (vd `"NR Hy4 (Hunyuan)"`).
- Model free nhóm cuối, prefix provider (OC/OR/NR/BA/AC/TR).
- **MỌI model phải có `base_instructions` + `model_messages.instructions_template`.** Thiếu 1 trong 2 → TOÀN BỘ catalog fail to load → lỗi `Error creating chat` / `model 'X' is missing both base_instructions and model_messages.instructions_template`.
- Model json chỉ load lúc khởi động → mọi sửa phải **fully restart Codex**.

### Các thay đổi đã thực hiện
- Thêm `NR Hy4`: slug `tencent/hy4-preview`, context 1M, max_output 64K, vision (CPA bọc lại).
- Gỡ `openrouter/z-ai/glm-5.3-flash` (giữ `openrouter:free`).
- Đổi `gemini-3.7-flash-high` → `gemini-3.8-flash-high` (không còn prefix `agy/`).
- `glm-5.2:free` → `glm-5.3:free`.
- `hy3:free`: context 192000, effective 65%, auto_compact 115000, max_output 128000.
- Gom nhóm NR về index 22 (sát nhóm `z-ai/glm-5.3-flash`, `~deepseek/...`).

---

## 6. CPA CONFIG (retry / timeout / free limits)

- CPA base `http://10.21.1.101:8317/v1`, key trong `config.toml` (regex `CLIPROXY_API_KEY\s*=\s*["']([^"']+)`, không hardcode).
- Đã set retry (script `reset_cpa_retries.py` chạy cả 2 máy):
  - `request_max_retries = 4`
  - `stream_max_retries = 5`
  - `stream_idle_timeout_ms = 300000`
- Free model limits chuẩn hóa (hy3:free 192K như §5).
- **LƯU Ý:** `ln.aicheap` đã bị gỡ khỏi CPA (hết credit) — local Codex không gọi aicheap. Đừng thêm lại trừ khi user cho phép.

---

## 7. CÁCH VERIFY TRÊN MỖI MÁY

```python
# check_103_integrity.py (viết lại cho đúng):
# - version: tên file *.manifest trong AppData\Local\CodexFromGithub
# - asar hash: SHA256 resources/app.asar
# - exe embedded hash: tìm blob JSON, so với header integrity của asar
# - config.toml: có [mcp_servers.codex_app]?
# - launch: Popen(ChatGPT.exe), sleep, tasklist, đọc log FATAL nếu có
```

Script tham khảo trong `tmp_scripts/`:
- `reorder_nr_hy4.py` — sửa slug + gom nhóm NR
- `fix_103_integrity_v2.py` / `v3.py` — vá tay exe (workaround §4.4)
- `check_103_integrity.py` — kiểm tra trạng thái
- `launch_103.py` — launch + capture log

---

## 8. OPEN ISSUES / TODO CHO DEV

1. ~~Sửa Patch B2~~ **ĐÃ LÀM** (§4.2b) — exe expected = SHA256 header JSON của asar, không phải SHA256 cả file.
   Còn lại: force rebuild để bản release khỏi cần vá tay exe từng máy.
2. Force rebuild CI để ra bản `-patched` sạch (verify bằng smoke-test launch trong CI).
3. Bật notification: add secrets SMTP (`MAIL_TO`, `MAIL_SMTP_HOST`, ...) vào repo, HOẶC user tự bật GitHub Watch Releases + Actions failure notify (không cần sửa code).
4. Xem xét đưa catalog + config.toml vào repo để sync tự động thay vì scp thủ công 2 máy.

---

## 9. FILE THAM KHẢO TRONG REPO

- `patches/patch_codex_asar_reconnect_clear.py` — đã sửa regex pag_cancel (§3.1)
- `patches/patch_codex_electron_fuse.py` — hardened (chỉ skip khi không có fuse sentinel)
- `patches/patch_codex_exe_asar_integrity_hash.py` — **Patch B2, ĐÃ SỬA đúng thuật toán (§4.2b)**
- `apply-all-patches.ps1` — gọi B2 cuối cùng
- `tmp_scripts/*.py` — script chạy tay trên 2 máy

---
*Handoff tổng hợp từ session làm việc Nyx + user (2026-09-02 → 09-04). Secrets đã được redact.*

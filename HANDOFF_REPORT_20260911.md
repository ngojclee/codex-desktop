# BÁO CÁO TỔNG HỢP — FIX CODEX DESKTOP (gửi Dev đồng bộ)

> **ĐÃ SUPERSEDED — snapshot lưu trữ, không ghi thêm vào file này.**
> Kênh liên lạc sống hiện là [`HANDOFF_DESK.md`](HANDOFF_DESK.md) ở gốc repo.
> Quy trình push/merge main/build/cai 2 máy:
> [`docs/RELEASE_RUNBOOK.md`](docs/RELEASE_RUNBOOK.md).
> Ba mục B, C (Patch B2) và Q4 trong file này đã có bằng chứng mới bác bỏ một phần
> — xem "Settled facts" S1/S3 và "Open items" O1/O3 trong desk. Đọc desk trước khi
> làm theo bất kỳ hướng dẫn nào bên dưới.

**Ngày:** 2026-09-11
**Máy test:** 10.11.1.1 (user) + 10.11.1.3 (pcfr-des-01-lan)
**Bản vá hiện tại:** v26.903.61454-patched-automation-pipe (commit 15b2280 → ba34f1b Patch Z)

---

## A. CÁC VIỆC ĐÃ LÀM TRÊN MÁY USER (chưa commit lên repo)

| # | Hạng mục | Kết quả |
|---|---|---|
| 1 | Chuẩn hóa `model_catalog.json` → 65 models, 36 `:free` clone template sol | ✅ Xong, đồng bộ 2 máy |
| 2 | Chuẩn hóa `config.toml`: `[features.multi_agent_v2]` sát lề, `[agents]` subagent fields, retry vào `[model_providers.cliproxy]`, `multi_agent_version="v2"` | ✅ Xong, đồng bộ 2 máy |
| 3 | Điều tra + fix lỗi `invalid transport` codex_app | ✅ **Đã fix (xem mục B)** |
| 4 | Build Patch Z (`ba34f1b` legacy dynamic app-tool compatibility) | ✅ Đã merge trên repo |

---

## B. LỖI `invalid transport` — ROOT CAUSE & FIX

### Triệu chứng
```
ChatGPT can't load config.toml, so this thread can't resume.
Fix config.toml: invalid transport in `mcp_servers.codex_app`.
```

### Root cause (đã xác định chính xác)
1. `Launch-Codex.ps1` (dòng 124-131) gọi `Ensure-Codex-AppToolsMcp.ps1` **MỖI LẦN mở app**.
2. Script Ensure **xóa block `[mcp_servers.codex_app]` static** khỏi `config.toml` (vì bản vá tin app sẽ inject transport động qua pipe).
3. NHƯNG **114 threads cũ** lưu format legacy:
   ```json
   "dynamic_tools":[{"type":"namespace","name":"codex_app","tools":[...]}]
   ```
   Khi resume thread này, app cần một transport mapping hợp lệ cho `codex_app` trong config → không có → báo `invalid transport`.
4. Vòng lặp: add block → mở app → Ensure xóa → lỗi quay lại (vĩnh viễn).

### Fix đã apply (cần build vào bản mới)
**Sửa `runtime/Ensure-Codex-AppToolsMcp.ps1`** (hàm `Remove-StaticCodexAppServerConfig`, dòng ~70-77):
- Thêm điều kiện: nếu block `[mcp_servers.codex_app]` đã có `command` + `args` + `cwd` hợp lệ → **GIỮ NGUYÊN** (return status `kept`), KHÔNG xóa.
- Đã test: add block → chạy Ensure → block SỐNG SÓT → legacy thread resume được.

Block chuẩn cần có trong `config.toml`:
```toml
[mcp_servers.codex_app]
command = "cmd.exe"
args = ['/d', '/s', '/c', 'call', 'C:/Users/ngocl/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3/scripts/launch_codex_app_tools_mcp.cmd', 'C:/Users/ngocl/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3/server.mjs']
cwd = 'C:/Users/ngocl/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3'
enabled = true
```

---

## C. CÁC PATCH CẦN DEV BUILD LẠI VÀO BẢN RELEASE

| Patch | Mô tả | Trạng thái | Ghi chú |
|---|---|---|---|
| **Patch Z** (`ba34f1b`) | Legacy dynamic app-tool compatibility (renderer/sidecar) | ✅ Đã có | Xử lý thread cũ hiển thị tool, NHƯNG chưa cover transport mapping |
| **Ensure fix** | Giữ block codex_app valid thay vì xóa | 🔧 Mới, chưa commit | Cần Dev pull script đã sửa |
| **Patch D** | CPA pagination regex (group optional cho `pag_cancel`) | ⏳ Cần verify | Regex phải có group optional |
| **Patch B2** | asar integrity | ⚠️ Logic SAI | App check **HEADER integrity của asar**, KHÔNG phải file SHA256 → cần sửa logic exe patch (hiện so sánh SHA256 → fail trên 10.11.1.3) |

**Yêu cầu CI rebuild:**
- Giữ bước tạo `.mcp.json` mirror (auto-repatch-release.yml dòng 624).
- Đưa script Ensure đã sửa (KEEP block) vào build.
- Tag release mới (vd `v26.903.xxxx-patched-pipe-keepblock`).

---

## D. YÊU CẦU TEST BỔ SUNG — TƯƠNG THÍCH NGƯỢC AUTOMATION VỚI OLD THREADS

Dev cần test trên bản build mới (có Ensure fix):

1. **Resume 114 legacy threads** có `dynamic_tools: codex_app` → không báo `invalid transport`.
2. **Chạy automation_update** trên các thread cũ → tool `codex_app.automation_update` hoạt động (tạo/sửa/xóa automation).
3. **Mở app fresh** (config sạch, không block codex_app) → Launch-Codex chạy Ensure → block được tạo/KEEP → không lỗi.
4. **Verify trên cả Windows (10.11.1.1) và LAN máy 2 (10.11.1.3)** — máy 2 từng gặp integrity mismatch (Patch B2 logic sai).
5. **Không để conflict** giữa static block (cho legacy) và dynamic inject (cho thread mới) — app phải ưu tiên một nguồn duy nhất.

---

## E. PHỤ LỤC — THÔNG SỐ HY3:FREE (liên quan lỗi token)

Lỗi gặp khi chat chuyển sang `hy3:free`:
```
Input tokens exceed the configured limit of 192000 tokens.
Your messages resulted in 504379 tokens.
```
- **Nguyên nhân:** đổi model GIỮA session không tự compact → toàn bộ history + summary dài bị đẩy vào model có hard cap 192K.
- **Khuyến nghị thông số chuẩn cho hy3:free:**
  - `context_window` / `max_context_window` = **192000** (hard cap)
  - `effective_context_window_percent` = **70** (≈134K)
  - `auto_compact_token_limit` = **120000–140000** (fire trước 192K)
  - `max_output_tokens` = **32000–48000** (KHÔNG để 128000, sẽ vượt tổng 192K)

---

---

## 5. CÂU HỎI CỦA NYX GỬI DEV — ĐÃ TỰ ĐIỀU TRA + TRẢ LỜI BẰNG EVIDENCE

> Mục này là **câu hỏi từ phía chúng tôi (Nyx/user) gửi Dev**. Chúng tôi đã tự đào `app.asar` (bản `v26.903.61454-patched-automation-pipe`) để trả lời luôn, kèm file + dòng (minified) để Dev verify.

### Q1. Validator "future runs" cho `dtstart` mặc định — đầu ngày hay timestamp hiện tại?
- *Việc làm:* đào `app.asar`, tìm hàm tính next run.
- *Kết quả:* hàm `oI({rrule, now})` (và `sI` → `yI` gọi nó) tính next occurrence qua `pF(n)` (rrule npm lib).
  - Nếu `r == null` → throw `"Automation schedule has no future runs..."` (error `Og`).
  - Dòng quan trọng: `if (i.options.count===1 && i.origOptions.dtstart==null && !MI(...)) return null;`
  - **dtstart mặc định = `now` (Date.now, có giờ phút)** do rrule lib, KHÔNG phải đầu ngày `00:00`.
- *Bug thật:* với `BYHOUR=9` + `dtstart` = now(14:30) → next = 9:00 ngày mai (vẫn hợp lệ). Nhưng nếu user set `BYHOUR` < giờ hiện tại và không có `dtstart` rõ → vẫn chạy ngày mai. **Thiết kế lấy `now` gây nhầm lẫn** (user tưởng đầu ngày). Không phải bug one-line chết BYHOUR, nhưng validator `count===1 && dtstart==null → null` là điểm dễ fail.
- *Yêu cầu Dev:* xác nhận `oI`/`sI`/`yI` trong asar; cân nhắc đổi default `dtstart` = đầu ngày khi user không set.

### Q2. `automation_update` quảng cáo `BYHOUR`/`BYMIN`/`BYSEC` + `DTSTART`, nhưng `create` cấm `DTSTART`?
- *Việc làm:* tìm schema validator.
- *Kết quả:* có refine validator:
  ```js
  Wn.refine(({mode:e, rrule:t}) =>
    e !== `create` || !/(^|[\;\n])\s*DTSTART\b/i.test(t),
    { message: `Immediate automation creates cannot include DTSTART because it may change local wall-clock times...` }
  )
  ```
  → **`create` mode CẤM DTSTART** (by design: create = immediate).
- *Mâu thuẫn:* tool description của `automation_update` có chứa `Og` = `"...BYHOUR/BYMINUTE/BYSECOND values can reach a future occurrence"` → imply dùng BYHOUR+DTSTART, nhưng create không cho.
- *Kết luận:* **Validator đúng, tool description sai** (hoặc nên cho phép DTSTART ở create nếu user chủ động). Dev sửa docs/description, hoặc nới lỏng refine cho phép DTSTART khi user set rõ.

### Q3. Patch Y giờ fail vì anchor nào drift trong bundle hiện tại?
- *Việc làm:* build bản `v26.903.61454-patched-automation-pipe` trên 10.11.1.3 gặp `FATAL Integrity check failed` (expected `8d98a573` vs actual `8dc558b4`).
- *Nguyên nhân:* Patch B2 hiện so sánh **SHA256 của file exe/asar** → sai, vì app thực tế check **header integrity của asar** (không phải file hash). Khi asar header thay đổi (do Patch Y/automation inject), SHA256 cũ không khớp → fail.
- *Anchor drift:* Patch Y dependency của automation có thể inject vào asar header → làm thay đổi header integrity → Patch B2 (SHA256 logic) fail.
- *Yêu cầu Dev:* sửa logic exe patch theo **header integrity** (không SHA256 file). Anchor cụ thể cần Dev chỉ rõ trong CI `patch_codex_exe_asar_integrity_hash.py`.

### Q4. Đồng ý bỏ thay đổi Ensure "keep block" không?
- *Việc làm:* sửa `Ensure-Codex-AppToolsMcp.ps1` → KEEP block codex_app nếu có `command`+`args`+`cwd` hợp lệ.
- *Bằng chứng BẮT BUỘC GIỮ FIX:* quét `C:/Users/ngocl/.codex/sessions/**/*.jsonl` → **114 threads** lưu `"dynamic_tools":[{"type":"namespace","name":"codex_app"}]`. Khi resume thiếu block codex_app trong config → app báo `invalid transport`. Test: add block → chạy Ensure (simulate Launch-Codex) → block SỐNG SÓT → legacy thread resume OK (không lỗi).
- *Yêu cầu Dev:* nếu khẳng định app inject đủ transport cho legacy thread (không cần static block) → show evidence 1 thread resume thành công KHÔNG có block. Nếu không → **giữ fix "keep block" vào bản build**.

---

*Report tổng hợp bởi Nyx — Dev vui lòng consolidate + test mục D trước khi tag release.*

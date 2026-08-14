# HOSxP Drug Refer Agent

Current version: **1.2.0**

Secure local web service สำหรับตรวจการเปลี่ยนแปลงของ `opitemrece` บน MariaDB
Slave, ดาวน์โหลด SQL จาก Drug Refer API และส่ง JSON ไปยัง API พร้อม local retry
outbox ที่ลบ VN เฉพาะหลัง API ตอบ `ok: true`

## Start web service

```bash
cd /Users/manoth/Desktop/drug-refer-back
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m agent.start
```

Agent เปิดที่ `http://127.0.0.1:8765` และพยายามเปิด default browser ให้อัตโนมัติ

ตรวจสถานะและเลขรุ่นได้ที่ `http://127.0.0.1:8765/healthz` โดย endpoint นี้จะตอบ
HTTP 503 เมื่อ web state อ่านไม่ได้ หรือ polling worker หยุดหลังตั้งค่าครบ เพื่อให้
Windows supervisor เริ่ม service ใหม่ได้

หากไม่ต้องการเปิด browser:

```bash
python -m agent.start --no-browser
```

## First-time setup

1. Login ด้วย `admin / admin`
2. เปลี่ยนรหัสผ่านทันที รหัสใหม่ต้องยาวอย่างน้อย 8 ตัว มีตัวพิมพ์ใหญ่
   ตัวพิมพ์เล็ก ตัวเลข และอักขระพิเศษ
3. ใส่การเชื่อมต่อ HOSxP แล้วกด **ทดสอบการเชื่อมต่อ** ปุ่มบันทึกจะเปิด
   เฉพาะเมื่อทดสอบค่าชุดเดียวกันสำเร็จภายใน 10 นาที
4. ใส่ API username/password ระบบจะ Login, รับ JWT และ GET SQL
5. เมื่อครบ Agent จะเริ่ม background polling และเปิดหน้า realtime logs

ระบบปฏิเสธการ bind ไปยัง non-loopback address ขณะที่รหัสเริ่มต้น `admin/admin`
ยังไม่ถูกเปลี่ยน

## Processing flow

```text
Every polling round: read-only snapshot opitemrece -> hash diff
Startup and every hour: Login + GET SQL -> keep last-known-good SQL in RAM/disk
No INSERT / UPDATE / DELETE_INFERRED -> wait for the next round
Changes found -> collect unique VNs -> use cached SQL immediately
Events for the same VN are coalesced until the VN is quiet for 3 seconds
Expand the VN predicate to a bound IN (...) batch -> run read-only detail query
No detail rows -> close the VN without delivery login or POST
Rows found -> fresh Login -> POST JSON array -> clear only acknowledged VNs
JSON preview -> Logs page + agent/logs/post-preview.jsonl
```

รูปแบบ `body` ที่ Preview และ POST จริงเป็น JSON array โดยตรงตามสัญญาของ API:

```json
[
  {"vn": "690720153609", "hn": "000012345"}
]
```

Agent จะถือว่าส่งสำเร็จเมื่อ HTTP สำเร็จและ response JSON มี `ok: true`
เท่านั้น หากได้ `ok: false`, response ไม่ใช่ JSON หรือเชื่อมต่อผิดพลาด จะเก็บ VN
ไว้ใน local outbox เพื่อส่งใหม่รอบถัดไป

POST ไปยัง `/syncData/query/2` ด้วย JWT ของ event batch เดียวกัน หน้า API สามารถเลือก
`LIVE` เพื่อส่งจริงหรือ `DRY RUN` เพื่อแสดง Preview โดยไม่ส่งข้อมูล

## Stored files

ขณะรันจาก source ค่า default อยู่ภายใต้โฟลเดอร์ `agent`:

- `agent/data/web.db`: user, audit และ encrypted settings
- `agent/data/master.key`: encryption/session master key, permission `0600`
- `agent/data/polling.db`: snapshot และ local event outbox
- `agent/data/remote-query-1.json`: SQL cache ไม่มี JWT
- `agent/logs/post-preview.jsonl`: JSON ที่ตั้งใจจะ POST ในอนาคต

`post-preview.jsonl` เก็บจริงเพียง 20 เคสล่าสุด โดยเขียนทับแบบ atomic และใช้ file lock
ร่วมกันระหว่าง worker กับหน้าเว็บ ส่วน `agent.log` หมุนไฟล์ที่ 2 MB และสำรอง 3 ไฟล์
(รวมสูงสุดประมาณ 8 MB) จึงไม่เติบโตโดยไม่มีขีดจำกัด แม้ปิด Browser Agent ยังเขียน
Log ลงดิสก์ต่อ เมื่อเปิดหน้า Logs ใหม่จะโหลด 200 บรรทัดล่าสุดจากดิสก์ก่อนต่อ
realtime stream และจำกัด DOM ใน Browser ไว้สูงสุด 500 บรรทัด

DB/API credentials ถูกเข้ารหัสด้วย Fernet และรหัสผ่านผู้ดูแล hash ด้วย Argon2id
JWT อยู่ใน memory เท่านั้น Session cookie เป็น HttpOnly/SameSite และทุก form
ตรวจ CSRF ฝั่ง server

ไฟล์ JSON preview มีข้อมูลผู้ป่วย แม้ permission เป็น `0600` ก็ควรกำหนดอายุ
การเก็บและระบบสำรองข้อมูลก่อน production

## Environment options

ไม่ต้องใส่ DB/API password ใน `.env` อีก เพราะตั้งผ่านเว็บและเก็บแบบเข้ารหัส

```env
WEB_HOST=127.0.0.1
WEB_PORT=8765
AUTO_OPEN_BROWSER=true
SESSION_SECURE_COOKIE=false

POLL_SECONDS=5
VN_DEBOUNCE_SECONDS=3
MAX_VNS_PER_CYCLE=100
DB_QUERY_VN_BATCH_SIZE=25
DB_QUERY_RETRIES=2
DB_READ_TIMEOUT_SECONDS=120
API_POST_VN_BATCH_SIZE=25
QUERY_REFRESH_SECONDS=3600
LOOKBACK_DAYS=1
DELETE_CONFIRM_ROUNDS=3
REQUIRE_SLAVE_HEALTH=false

API_BASE_URL=https://cpho.dentdata.net
API_TOKEN_HEADER=Authorization
API_TOKEN_SCHEME=Bearer
```

หาก `.env` เดิมยังมี `DB_PASSWORD` หรือ `API_PASSWORD` ให้ลบหลังตั้งค่าผ่านหน้าเว็บ
และเปลี่ยนรหัสผ่านที่เคยเปิดเผยออกนอกเครื่อง

## Network deployment

ค่า default รับเฉพาะ `127.0.0.1` หากต้องเปิดให้เครื่องอื่นใน LAN ใช้งาน:

1. ทำ first-time setup ผ่าน localhost ให้เสร็จก่อน
2. วาง reverse proxy ที่มี HTTPS หน้า Agent
3. ตั้ง `SESSION_SECURE_COOKIE=true`
4. จำกัด firewall เฉพาะ IP ผู้ดูแล
5. จึงเปลี่ยน `WEB_HOST=0.0.0.0`

ไม่ควรเปิด port 8765 สู่ Internet โดยตรง

## JWT and delivery behavior

Agent จะ Login + GET SQL ตอนเริ่มและทุก `QUERY_REFRESH_SECONDS` (ค่าเริ่มต้น 1 ชั่วโมง)
จากนั้นเก็บ last-known-good SQL ใน RAM และไฟล์ cache หากข้อความ SQL ไม่เปลี่ยนจะไม่เขียน
cache ใหม่ ระหว่างชั่วโมง Event จะใช้ SQL ใน RAM ได้ทันทีโดยไม่เรียก API ก่อน query
เมื่อ detail SQL คืนข้อมูลเท่านั้น Agent จึง Login ใหม่สำหรับ delivery แล้ว POST JSON array
หาก detail SQL ไม่คืนข้อมูลจะปิดงานด้วยสถานะ `skipped_no_data` โดยไม่ Login เพื่อส่งและ
ไม่ POST หาก SQL ใน RAM query ไม่ผ่าน จะบังคับ GET SQL ใหม่หนึ่งครั้งแล้ว retry query

Agent ถือว่าส่งสำเร็จเมื่อ HTTP สำเร็จพร้อม response `ok: true` เท่านั้น หากล้มเหลว VN
จะยังอยู่ใน local SQLite outbox เพื่อส่งใหม่รอบถัดไป โหมด `DRY RUN` ไม่มี API POST และ
เก็บ payload ไว้ตรวจในหน้า Logs

สำหรับฐานจริงที่มี Event จำนวนมาก Agent จะดึงงานจาก outbox ไม่เกิน
`MAX_VNS_PER_CYCLE` ต่อรอบ แบ่ง detail SQL เป็นชุดละ `DB_QUERY_VN_BATCH_SIZE` VN
โดยเปิด connection ใหม่ต่อชุด และแบ่ง JSON array ที่ POST เป็นชุดละ
`API_POST_VN_BATCH_SIZE` VN เพื่อไม่ให้ MariaDB packet หรือ HTTP request ใหญ่เกินไป
หาก connection เสียระหว่าง query จะลองใหม่ด้วย connection ใหม่ตาม `DB_QUERY_RETRIES`
ส่วน VN ที่ยังไม่ถึงคิวหรือส่งไม่สำเร็จจะไม่ถูกลบจาก outbox

การอ่านข้อความจาก HOSxP ใช้ CP874 ซึ่งเข้ากันได้กับอักษรไทย TIS-620 และรองรับ
legacy bytes ที่พบในฐานจริง เช่น `0xA0` โดยแปลง non-breaking space เป็นช่องว่างปกติ
และแทนเฉพาะ byte ที่เสีย จึงไม่ทำให้ query batch ทั้งชุดล้มเพราะข้อมูลข้อความหนึ่งช่อง

## Windows EXE

การสร้าง EXE ต้องทำบน Windows ใช้ไฟล์ `build_windows.bat` และคู่มือ
[WINDOWS_BUILD.md](WINDOWS_BUILD.md) ผลลัพธ์อยู่ที่ `dist\\DrugReferAgent.exe`
ข้อมูลถาวรของ EXE อยู่ใน `%LOCALAPPDATA%\\DrugReferAgent` จึงไม่หายจากพื้นที่ชั่วคราว
ของ PyInstaller one-file ตัว EXE เป็น single-instance background app ไม่มี Console:
ครั้งแรกจะเริ่ม service และเปิดเว็บ ส่วนการ Double-click ซ้ำจะเปิดเว็บของ service เดิม
เท่านั้น ค่าเริ่มต้นลงทะเบียน `AUTO_START_WINDOWS=true` เพื่อเริ่ม Agent หลังผู้ใช้ Windows
Sign in ภายหลัง Restart เครื่อง ผู้ดูแลเปิดหรือปิดได้จากเมนู **API** ช่อง
**เริ่ม Agent พร้อม Windows** โดยระบบจะจัดการ Startup Registry ของผู้ใช้ปัจจุบันให้เอง
ตัว EXE รุ่นนี้มี Supervisor แยกจาก web-service process คอยตรวจ `/healthz` และเปิด
service ใหม่อัตโนมัติเมื่อ process หลุดหรือไม่ตอบสนอง การปิด Browser จึงไม่หยุด Agent
บันทึกการกู้ service อยู่ที่
`%LOCALAPPDATA%\\DrugReferAgent\\logs\\supervisor.log`

หาก browser มี session cookie จากรุ่นเก่าหรือ cookie เสีย Agent จะล้าง session และเปิด
หน้า Login ใหม่เอง แทนการตอบหน้าเปล่า `Internal Server Error`

ใน Windows EXE ไฟล์ template/static จะถูกคัดลอกจากพื้นที่ชั่วคราวของ PyInstaller ไปยัง
`%LOCALAPPDATA%\\DrugReferAgent\\runtime-assets\\<version>` ตอนเริ่ม service ทุกครั้ง
หน้าเว็บจึงยังทำงานแม้ Windows หรือโปรแกรม cleanup ลบโฟลเดอร์ `_MEI...` หลังเปิด Agent
ต่อเนื่องหลายวัน

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -v
python -m compileall -q agent tests
```

## Command-line dry run

CLI เดิมยังใช้งานได้:

```bash
python -m agent.main --validate-sql
python -m agent.main --fetch-query
python -m agent.main --once
python -m agent.main
```

Web service เป็นวิธีใช้งานหลัก ส่วน CLI เหมาะกับ diagnosis และ automated runbook

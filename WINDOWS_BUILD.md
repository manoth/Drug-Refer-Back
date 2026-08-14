# Build Drug Refer Agent เป็น Windows EXE

เอกสารนี้ใช้กับ Agent รุ่น 1.4.0

ต้อง build บน Windows เท่านั้น เพราะ PyInstaller ไม่รองรับการสร้าง Windows EXE
จาก macOS โดยตรง แนะนำ Windows 10/11 แบบ 64-bit และ Python 3.11–3.13 แบบ 64-bit

## 1. เตรียมเครื่อง Build

1. ติดตั้ง Python 3.11 แบบ 64-bit จาก https://www.python.org/downloads/windows/
2. ระหว่างติดตั้งเลือก `Add python.exe to PATH`
3. คัดลอกโฟลเดอร์โปรเจกต์นี้ไปยังเครื่อง Windows
4. เปิด Command Prompt ในโฟลเดอร์โปรเจกต์

## 2. สร้าง EXE

```bat
py -3.11 -m venv .venv
build_windows.bat
```

สคริปต์จะติดตั้ง dependency, รัน tests และสร้างไฟล์:

```text
dist\DrugReferAgent.exe
```

## 3. นำไปใช้งาน

1. คัดลอก `DrugReferAgent.exe` ไปยังเครื่อง Agent ของโรงพยาบาล
2. Double-click ไฟล์ EXE
3. Agent จะทำงานเบื้องหลังโดยไม่มีหน้าต่าง Terminal และ Browser จะเปิด
   `http://127.0.0.1:8765`
4. Login ครั้งแรกด้วย `admin / admin` และเปลี่ยนรหัสผ่านทันที
5. ตั้งค่าฐานข้อมูล HOSxP แล้วทดสอบการเชื่อมต่อ
6. ตั้งค่า API, เลือก `LIVE`, ติ๊กยืนยัน และบันทึก

การเปิด EXE ครั้งแรกจะลงทะเบียน Startup ของผู้ใช้ Windows ปัจจุบันโดยอัตโนมัติ
หลัง Restart เครื่อง Agent จะเริ่มเบื้องหลังทันทีเมื่อผู้ใช้นี้ Sign in โดยไม่เปิด Browser
หาก Agent ทำงานอยู่แล้ว การ Double-click EXE จะเปิดหน้าเว็บของตัวเดิมเท่านั้นและจะไม่สร้าง
worker ซ้ำ

Windows EXE ใช้ Supervisor แยกจาก Web-service child เมื่อ child crash หรือไม่ตอบ
Health check ต่อเนื่อง ระบบจะปิด child ที่ค้างและเปิดใหม่อัตโนมัติ คิว VN และการตั้งค่า
ยังอยู่ใน SQLite เดิมจึงไม่สูญหาย การปิด Browser ไม่มีผลต่อ Supervisor หรือ Agent
หากต้องการหยุดทั้งระบบต้องกด **Exit Agent** เท่านั้น

บันทึกการกู้คืนของ Supervisor อยู่ที่:

```text
%LOCALAPPDATA%\DrugReferAgent\logs\supervisor.log
```

เปิดหรือปิดการเริ่มพร้อม Windows ได้ที่เมนู **API** ในช่อง
**เริ่ม Agent พร้อม Windows** แล้วกดบันทึก ระบบจะเพิ่มหรือลบค่าใน Startup ให้ทันที
โดยไม่ต้องเปิด Registry หรือแก้ไฟล์ `.env` เอง การตั้งค่านี้เป็นแบบรายผู้ใช้ Windows
(HKCU) จึงไม่ต้องใช้สิทธิ์ Administrator

หากต้องการปิดโปรเซสเพื่ออัปเดต EXE ให้กด **Exit Agent** ที่หน้า Logs เพื่อหยุด
worker และ web service อย่างปลอดภัย หากหน้าเว็บเข้าไม่ได้จึงค่อยใช้:

```bat
taskkill /F /IM DrugReferAgent.exe
```

ข้อมูลถาวรไม่ถูกเก็บในพื้นที่ชั่วคราวของ EXE แต่เก็บที่:

```text
%LOCALAPPDATA%\DrugReferAgent\data
%LOCALAPPDATA%\DrugReferAgent\logs
```

Log สำหรับ background process อยู่ที่
`%LOCALAPPDATA%\DrugReferAgent\logs\agent.log` ขนาดไฟล์ละ 2 MB และหมุนสำรอง
3 ไฟล์ (รวมสูงสุดประมาณ 8 MB) แม้ปิด Browser ระบบยังเขียน Log ต่อ เมื่อเปิดหน้า Logs
ใหม่จะอ่าน 200 บรรทัดล่าสุดจากดิสก์และต่อ realtime stream โดย Browser แสดงไม่เกิน
500 บรรทัด จึงไม่กินหน่วยความจำเพิ่มไปเรื่อย ๆ

ต้องสำรองทั้งโฟลเดอร์ `DrugReferAgent` โดยเฉพาะ `master.key`, `web.db` และ
`polling.db` หาก master key สูญหายจะถอดรหัส credential เดิมไม่ได้

## การตั้งค่าเพิ่มเติม

ค่าเริ่มต้นเปิดเฉพาะ localhost และเปิด LIVE POST หลังตั้งค่าครบ หากต้องปรับ port
หรือกลับไปทดสอบ ให้สร้าง `.env` ไว้ข้าง EXE เช่น:

```env
WEB_HOST=127.0.0.1
WEB_PORT=8765
AUTO_OPEN_BROWSER=true
AUTO_START_WINDOWS=true
DRY_RUN=true
MAX_VNS_PER_CYCLE=100
DB_QUERY_VN_BATCH_SIZE=25
DB_QUERY_RETRIES=2
DB_READ_TIMEOUT_SECONDS=120
API_POST_VN_BATCH_SIZE=25
QUERY_REFRESH_SECONDS=3600
```

ค่าในหน้า API จะมีลำดับความสำคัญเหนือ `DRY_RUN` และ `AUTO_START_WINDOWS`
หลังจากบันทึกแล้ว ค่า `AUTO_START_WINDOWS` จึงเป็นเพียงค่าเริ่มต้นก่อนตั้งค่าผ่านหน้าเว็บ

ค่าชุดด้านบนเหมาะเป็นจุดเริ่มต้นสำหรับฐานจริงที่มี Event มาก: ประมวลผลไม่เกิน
100 VN ต่อรอบ, query ฐานข้อมูลและ POST API ครั้งละ 25 VN หากยังพบปัญหา packet
ให้ลด `DB_QUERY_VN_BATCH_SIZE` เป็น `10` หรือ `5` โดยไม่เพิ่ม
`MAX_VNS_PER_CYCLE` จนกว่าจะตรวจสอบโหลดของฐานข้อมูลแล้ว

## อัปเดต EXE โดยไม่ทำคิวสูญหาย

1. กด **Exit Agent** ในหน้า Logs และตรวจว่าโปรเซสรุ่นเดิมปิดแล้ว
2. สำรอง `%LOCALAPPDATA%\DrugReferAgent` ทั้งโฟลเดอร์
3. Build source รุ่นใหม่ แล้วแทนที่เฉพาะ `DrugReferAgent.exe`
4. ห้ามลบ `polling.db`, `web.db` หรือ `master.key`
5. เปิด EXE ใหม่ งาน VN ที่ยังไม่สำเร็จใน `polling.db` จะถูกส่งต่อในรอบถัดไป
6. เปิด `http://127.0.0.1:8765/healthz` และตรวจว่าแสดง `"version":"1.4.0"`

## ข้อควรระวัง

- อย่าวาง EXE หรือโฟลเดอร์ข้อมูลบน shared folder ที่ทุกคนเข้าถึงได้
- ไม่ควรเปิด port 8765 สู่อินเทอร์เน็ตโดยตรง
- หากต้องให้เครื่องอื่นใน LAN เข้าใช้งาน ควรวาง HTTPS reverse proxy และ firewall
- Windows อาจแสดง SmartScreen เพราะ EXE ยังไม่มี code-signing certificate
- การสร้าง EXE รุ่นใหม่ไม่ลบข้อมูลเดิมใน `%LOCALAPPDATA%`

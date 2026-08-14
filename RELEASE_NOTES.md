# Drug Refer Agent v1.1.0

รุ่นแก้เสถียรภาพสำหรับเครื่องที่เปิด Agent ต่อเนื่องหลายวัน

- แก้ `/login` ตอบ `Internal Server Error` เมื่อ browser ส่ง session cookie รุ่นเก่าหรือ payload เสีย โดยล้าง session และสร้าง cookie ใหม่อัตโนมัติ
- `/healthz` ตรวจทั้ง SQLite web state และ polling worker เพื่อให้ Windows supervisor กู้ service ที่เว็บยังตอบแต่ worker หยุดทำงานได้
- เพิ่มเลขรุ่นใน health response สำหรับตรวจสอบว่าเครื่องกำลังรันไฟล์ใหม่
- เพิ่ม regression tests สำหรับ session payload เสียแบบ base64, JSON และ UTF-8

## อัปเดตจากรุ่นเดิม

1. กด **Exit Agent** ในหน้า Logs ถ้าหน้าเว็บยังเข้าได้ หรือใช้ `taskkill /F /IM DrugReferAgent.exe`
2. สำรอง `%LOCALAPPDATA%\DrugReferAgent` ทั้งโฟลเดอร์
3. ดาวน์โหลด EXE ด้านล่างแล้วนำไปแทนไฟล์เดิม โดยไม่ลบ `data`, `web.db`, `polling.db` หรือ `master.key`
4. เปิด EXE ใหม่และตรวจ `http://127.0.0.1:8765/healthz` ต้องเห็น `"version":"1.1.0"`

ไฟล์ `SHA256SUMS.txt` ใช้ตรวจความถูกต้องของไฟล์ดาวน์โหลดได้ รุ่นนี้ยังไม่มี code-signing certificate จึงอาจพบ Windows SmartScreen ตามปกติ

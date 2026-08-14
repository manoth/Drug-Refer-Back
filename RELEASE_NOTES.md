# Drug Refer Agent v1.2.0

รุ่นแก้ปัญหาหน้าเว็บ `Internal Server Error` หลังเปิด Agent ต่อเนื่องหลายวัน

- แก้ `jinja2.exceptions.TemplateNotFound` เมื่อ Windows หรือโปรแกรม cleanup ลบโฟลเดอร์ชั่วคราว `_MEI...` ของ PyInstaller one-file ขณะที่ Agent ยังทำงาน
- คัดลอก template และ static files ไปยัง `%LOCALAPPDATA%\DrugReferAgent\runtime-assets\1.2.0` และ refresh ทุกครั้งที่เริ่ม web-service child
- คงการแก้ session cookie และ health check จาก v1.1.0
- เพิ่ม regression test ที่ลบโฟลเดอร์ bundle จำลอง แล้วตรวจว่าหน้าเว็บยังอ่าน assets จากพื้นที่ถาวรได้

## อัปเดตจากรุ่นเดิม

1. ใช้ `taskkill /F /IM DrugReferAgent.exe` เพื่อให้แน่ใจว่า supervisor และ child รุ่นเดิมหยุดทั้งหมด
2. สำรอง `%LOCALAPPDATA%\DrugReferAgent` ทั้งโฟลเดอร์
3. นำ EXE v1.2.0 ไปไว้ในตำแหน่งถาวรแทนไฟล์เดิม แล้วเปิดจากตำแหน่งนั้น
4. ตรวจ `http://127.0.0.1:8765/healthz` ต้องเห็น `"version":"1.2.0"`

ห้ามลบ `data`, `web.db`, `polling.db` หรือ `master.key` ไฟล์ `SHA256SUMS.txt` ใช้ตรวจความถูกต้องของไฟล์ดาวน์โหลดได้

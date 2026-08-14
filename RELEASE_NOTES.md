# Drug Refer Agent v1.3.0

รุ่นอัปเดตแบบรับช่วงต่ออัตโนมัติ

- Double-click EXE รุ่นใหม่แล้ว launcher จะตรวจเลขรุ่นที่ port 8765
- ถ้ามีรุ่นเก่าทำงานอยู่ จะหยุดทั้ง supervisor และ web-service child เดิม รอ lock ถูกปล่อย แล้วเริ่มรุ่นใหม่ทันที
- ถ้าเป็นรุ่นเดียวกัน จะเปิดหน้าเว็บเดิมโดยไม่สร้าง worker ซ้ำ
- Windows Startup Registry จะเปลี่ยนไปชี้ EXE รุ่นใหม่หลังรับช่วงสำเร็จ
- รวมการแก้ persistent web assets จาก v1.2.0 และ session/health recovery จาก v1.1.0

## วิธีอัปเดต

1. ดาวน์โหลด EXE v1.3.0 ไปไว้ในตำแหน่งถาวรที่ต้องการใช้งาน
2. Double-click ไฟล์ใหม่ได้ทันที ไม่ต้องปิดรุ่นเดิมด้วยตนเอง
3. รอหน้าเว็บเปิด แล้วตรวจ `http://127.0.0.1:8765/healthz` ต้องเห็น `"version":"1.3.0"`

ข้อมูลเดิมใน `%LOCALAPPDATA%\DrugReferAgent` รวมถึง `web.db`, `polling.db` และ `master.key` จะถูกใช้ต่อโดยไม่ถูกลบ

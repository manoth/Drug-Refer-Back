# Drug Refer Agent v1.6.0

รุ่นเพิ่ม incremental master-data sync สำหรับ HOSxP

- อ่าน `drugitems` และ `s_drugitems` แบบ read-only ตอนเริ่ม Agent และทุก 1 ชั่วโมง
- รอบแรก sync ข้อมูลทั้งหมด จากนั้นใช้ SHA-256 ราย `icode` ส่งเฉพาะรายการใหม่/แก้ไข
- POST เป็น batch และบันทึกสถานะหลัง API ตอบ `ok: true` เท่านั้น งานค้างจึง retry ได้
- ความล้มเหลวของ master sync ไม่หยุด event flow `sys_drug_refer` เดิม
- ไม่ลบข้อมูล API เมื่อรายการหายจาก HOSxP ตามขอบเขตที่กำหนดเฉพาะเพิ่ม/แก้ไข
- แนบ migration `v1.6.0-master-drug-sync.sql` สำหรับสร้าง `s_drugitems` และ API query IDs 3/4
- คงระบบ takeover, background service, diagnostic logs และ automatic update จาก v1.5.0

ก่อนเปิดใช้ master sync ต้องรันไฟล์ migration กับฐาน `db_drug_refer` หนึ่งครั้ง

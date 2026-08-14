# Drug Refer Agent v1.4.0

รุ่นเพิ่ม Diagnostic Logs สำหรับวิเคราะห์และปรับปรุง Agent รุ่นถัดไป

- เก็บ ERROR/CRITICAL และ traceback แยกใน `error.log` แบบหมุนไฟล์ 2 MB สำรอง 5 ไฟล์
- เพิ่มปุ่ม **Diagnostic ZIP** ในหน้า Logs
- ZIP รวม error logs, supervisor logs, startup error, version/platform/worker status และ context ล่าสุด
- ปกปิด VN, HN, CID, HOS GUID, password-like fields และ bearer tokens อัตโนมัติ
- ไม่รวม `post-preview.jsonl` ซึ่งมีข้อมูล payload ผู้ป่วย
- รวม automatic version takeover จาก v1.3.0 และ persistent web assets จาก v1.2.0

## วิธีอัปเดต

วาง EXE v1.4.0 ในตำแหน่งถาวรแล้ว Double-click ได้ทันที ตัวใหม่จะหยุดรุ่นเก่าและรับช่วงทำงานอัตโนมัติ จากนั้นตรวจ `/healthz` ต้องเห็น `"version":"1.4.0"`

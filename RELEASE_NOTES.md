# Drug Refer Agent v1.5.0

รุ่นเพิ่มระบบตรวจและติดตั้ง GitHub Release อัตโนมัติ

- แสดงเลข version ที่ header, footer และ `/healthz`
- ตรวจ GitHub Release ล่าสุดเป็นระยะโดย cache ผลหนึ่งชั่วโมง
- เมื่อมีรุ่นใหม่ แสดง SweetAlert ถามผู้ดูแลก่อนอัปเดต
- ดาวน์โหลด Windows EXE ไปยังพื้นที่ถาวรและตรวจ SHA-256 ก่อนเปิดทุกครั้ง
- EXE ใหม่หยุด supervisor/child รุ่นเก่าและรับช่วง port, worker, Startup Registry ทันที
- หน้าเว็บรอ health check ของรุ่นใหม่และ reload อัตโนมัติ
- คง Diagnostic ZIP แบบปกปิดข้อมูลสำคัญจาก v1.4.0

หลังติดตั้ง v1.5.0 ครั้งแรก การอัปเดตรุ่นถัดไปทำได้จาก Alert ในหน้าเว็บ ไม่ต้องดาวน์โหลดหรือปิด process ด้วยตนเอง

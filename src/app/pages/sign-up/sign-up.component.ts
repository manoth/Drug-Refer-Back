import { Component, ElementRef, Inject, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { CryptoService } from 'src/app/services/crypto.service';
import { MainService } from 'src/app/services/main.service';
import Swal from 'sweetalert2';
declare const $: any;

@Component({
  selector: 'app-sign-up',
  templateUrl: './sign-up.component.html',
  styleUrls: ['./sign-up.component.scss']
})
export class SignUpComponent {

  public loading: boolean = false;
  public isSubmitted = false;
  public validateIDCard = { ok: false, message: 'กรุณากรอกเลขบัตรประชาชนที่ถูกต้อง' };
  public validateEmail = { ok: false, message: 'กรุณากรอกอีเมลที่ถูกต้อง' };
  public validateHospital: boolean = false;

  public member = new Member();
  public hospital: string = '';
  public hospitalList: any[] = [];
  public password: string = '';
  public repassword: string = '';

  @ViewChild('hospitalSelect') hospitalSelect!: ElementRef;

  ngAfterViewInit() {
    setTimeout(() => {
      if (this.hospitalSelect) {
        $(this.hospitalSelect.nativeElement).select2().on('change', (e: any) => {
          this.member.hospcode = e.target.value;
          this.updateErrorState();
        });
      }
    }, 1000);
  }

  updateErrorState() {
    const select2Selection = $(this.hospitalSelect.nativeElement)
      .next('.select2-container')
      .find('.select2-selection.select2-selection--single');
    if (!this.member.hospcode || this.member.hospcode === '') {
      this.validateHospital = true;
      select2Selection.css({
        'border-color': '#dd4b39',
        'box-shadow': 'none'
      });
    } else {
      this.validateHospital = false;
      select2Selection.css({
        'border-color': '',
        'box-shadow': ''
      });
    }
  }

  constructor(
    private router: Router,
    private main: MainService,
    private crypto: CryptoService
  ) {
    document.body.className = 'hold-transition login-page background-login';
    $(function () {
      $('.select2').select2();
      $('#cid').focus();
    });
    if (this.main.auth1) {
      this.router.navigate(['/']);
    }
    this.getHospital();
  }

  restrictInput(event: KeyboardEvent): void {
    const allowedKeys = ['Backspace', 'Tab', 'ArrowLeft', 'ArrowRight', 'Delete'];
    const key = event.key;
    if (!allowedKeys.includes(key) && (isNaN(Number(key)) || key === ' ')) {
      event.preventDefault(); // ป้องกันไม่ให้ป้อนข้อมูลที่ไม่ใช่ตัวเลข
    }
  }

  isValidateThaiIDCard(cid: string) {
    this.validateIDCard = { ok: false, message: 'กรุณากรอกเลขบัตรประชาชนที่ถูกต้อง' };
    if (cid.length >= 1) {
      this.main.get(`signUp/check/IDCard/${cid}`).then((res: any) => {
        this.validateIDCard = res;
      });
    }
  }

  isValidateEmail(email: string) {
    this.validateEmail = { ok: false, message: 'กรุณากรอกอีเมลที่ถูกต้อง' };
    if (email.length >= 1) {
      this.main.get(`signUp/check/Email/${email}`).then((res: any) => {
        this.validateEmail = res;
      });
    }
  }

  isValidatePassword(password: string) {
    const minLength = /.{8,}/;
    const hasUpperCase = /[A-Z]/;
    const hasLowerCase = /[a-z]/;
    const hasNumbers = /[0-9]/;
    const hasSpecialChar = /[!@#$%^&*(),.?":{}|<>]/;
    return minLength.test(password) &&
      hasUpperCase.test(password) &&
      hasLowerCase.test(password) &&
      hasNumbers.test(password) &&
      hasSpecialChar.test(password);
  }

  getHospital() {
    this.main.get(`signUp/list/hospital`).then((res: any) => {
      this.hospitalList = (res.ok) ? res.data : [];
    });
  }

  onSubmit(form: any): void {
    this.isSubmitted = true;
    this.updateErrorState();
    if (form.valid && this.validateIDCard.ok && this.validateEmail.ok && this.isValidatePassword(this.password) && this.password === this.repassword) {
      this.member.password = this.crypto.md5(this.password);
      this.member.username = this.member.email;
      this.loading = true;
      this.main.post('signUp', this.member).then((res: any) => {
        this.loading = false;
        Swal.fire({
          title: (res.ok) ? 'ลงทะเบียนสำเร็จ!' : 'เกิดข้อผิดพลาด!',
          icon: (res.ok) ? 'success' : 'error',
          text: res.message,
          allowOutsideClick: false
        }).then((result: any) => {
          if (result.isConfirmed) {
            this.router.navigate(['/signin']);
          }
        });
      });
    }
  }

}

export class Member {
  cid: string = '';
  pname: string = '';
  fname: string = '';
  lname: string = '';
  hospcode: string = '';
  username: string = '';
  password: string = '';
  email: string = '';
}

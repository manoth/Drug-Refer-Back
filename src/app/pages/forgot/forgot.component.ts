import { Component, Inject } from '@angular/core';
import { Router } from '@angular/router';
import { MainService } from 'src/app/services/main.service';
import Swal from 'sweetalert2';
declare const $: any;

@Component({
  selector: 'app-forgot',
  templateUrl: './forgot.component.html',
  styleUrls: ['./forgot.component.scss']
})
export class ForgotComponent {

  public loading: boolean = false;

  constructor(
    private router: Router,
    private main: MainService
  ) {
    document.body.className = 'hold-transition login-page background-login';
    if (this.main.auth1) {
      this.router.navigate(['/']);
    }
  }

  restrictInput(event: KeyboardEvent): void {
    const allowedKeys = ['Backspace', 'Tab', 'ArrowLeft', 'ArrowRight', 'Delete'];
    const key = event.key;
    if (!allowedKeys.includes(key) && (isNaN(Number(key)) || key === ' ')) {
      event.preventDefault(); // ป้องกันไม่ให้ป้อนข้อมูลที่ไม่ใช่ตัวเลข
    }
  }

}

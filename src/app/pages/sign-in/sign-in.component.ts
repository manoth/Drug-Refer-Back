import { Component, Inject } from '@angular/core';
import { Router } from '@angular/router';
import { JwtHelperService } from '@auth0/angular-jwt';
import { CryptoService } from 'src/app/services/crypto.service';
import { MainService } from 'src/app/services/main.service';
import Swal from 'sweetalert2';
declare const $: any;

@Component({
  selector: 'app-sign-in',
  templateUrl: './sign-in.component.html',
  styleUrls: ['./sign-in.component.scss']
})
export class SignInComponent {

  public loading: boolean = false;
  private jwtHelper = new JwtHelperService();
  public signIn = new SignIn();

  constructor(
    @Inject('ACCESSTOKEN') private accessTokenName: string,
    private router: Router,
    private main: MainService,
    private crypto: CryptoService
  ) {
    document.body.className = 'hold-transition login-page background-login';
    $(function () {
      $('#username').focus();
      $('input').iCheck({
        checkboxClass: 'icheckbox_square-blue',
        radioClass: 'iradio_square-blue',
        increaseArea: '15%'
      });
    });
    if (this.main.auth1) {
      this.router.navigate(['/']);
    }
  }

  onSubmit() {
    this.loading = true;
    if (this.signIn.username && this.signIn.password) {
      let data = {
        username: this.signIn.username,
        password: this.crypto.md5(this.signIn.password),
      }
      this.main.post('signIn', data).then((res: any) => {
        this.loading = false;
        if (res.ok) {
          localStorage.setItem(this.accessTokenName, res.token);
          this.router.navigate(['/']);
        } else {
          Swal.fire({
            icon: 'warning',
            title: res.message,
            allowOutsideClick: false
          });
        }
      });
    } else {
      this.loading = false;
      Swal.fire({
        icon: 'warning',
        title: 'กรุณากรอก Username และ Password ด้วย!',
        allowOutsideClick: false
      }).then(() => {
        (!this.signIn.username) ? $('#username').focus() : $('#password').focus();
      });
    }
  }

}

export class SignIn {
  username: string = '';
  password: string = '';
}
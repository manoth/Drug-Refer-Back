import { Component } from '@angular/core';
import { NgForm } from '@angular/forms';
import { CryptoService } from 'src/app/services/crypto.service';
import { MainService } from 'src/app/services/main.service';
import Swal from 'sweetalert2';
declare const $: any;

@Component({
  selector: 'app-member',
  templateUrl: './member.component.html',
  styleUrls: ['./member.component.scss']
})
export class MemberComponent {

  public account = this.main.jwtDecode;

  public memberList: any[] = [];
  public member: any = {};

  public isSubmitted: boolean = false;
  public password: string = '';
  public repassword: string = '';

  constructor(
    private crypto: CryptoService,
    private main: MainService,
  ) {
    this.getMemberList();
  }

  getMemberList() {
    this.memberList = [];
    this.main.get('setting/member/list').then((res: any) => {
      // console.log(res);
      this.memberList = (res.ok) ? res.data : [];
      $(document).ready(() => {
        $('#memberTable').DataTable({
          scrollX: false,
          lengthMenu: [20, 50, 100],
          autoWidth: false,
          destroy: true,
          searching: true
        });
      });
    });
  }

  onChangeMember(data: any, cidEnc: string) {
    return this.main.put(`setting/member/${cidEnc}`, data).then((res: any) => {
      Swal.fire({
        title: (res.ok) ? '' : 'เกิดข้อผิดพลาด!',
        icon: (res.ok) ? 'success' : 'error',
        text: res.message,
        showConfirmButton: !res.ok,
        timer: (res.ok) ? 1500 : 0
      });
      return res;
    });
  }

  onChangeMemberActive(cidEnc: string, event: Event) {
    const checkbox = event.target as HTMLInputElement;
    const data = { active: (checkbox.checked) ? 'Y' : 'N' };
    this.onChangeMember(data, cidEnc).then((res: any) => {
      checkbox.checked = (res.ok) ? checkbox.checked : !checkbox.checked;
    }).catch((err: any) => {
      checkbox.checked = checkbox.checked;
    });
  }

  onChangeMemberPassword(member: any, form: NgForm) {
    this.member = member;
    this.isSubmitted = false;
    form.reset();
    $('#modal-edit-password').modal({ backdrop: 'static', keyboard: false });
  }

  onSubmit(form: NgForm): void {
    this.isSubmitted = true;
    if (form.valid && this.isValidatePassword(this.password) && this.password === this.repassword) {
      const data = { password: this.crypto.md5(form.value.password) };
      this.onChangeMember(data, this.member.cidEnc).then((res: any) => {
        (res.ok) ? $('#modal-edit-password').modal('hide') : null;
      });
    }
  }

  onDeleteMember(member: any) {
    Swal.fire({
      icon: 'question',
      title: `คุณต้องการที่จะลบข้อมูลสมาชิก\n ${member.pname}${member.fname || member.hosname} ${member.lname} \nออกจากระบบ ใช่หรือไม่?`,
      showCancelButton: true,
      confirmButtonText: ' ใช่ ',
      cancelButtonText: 'ไม่ใช่',
      confirmButtonColor: "#3085d6",
      cancelButtonColor: "#d33",
      allowOutsideClick: false
    }).then((result) => {
      if (result.isConfirmed) {
        this.main.delete(`setting/member/${member.cidEnc}`, {}).then((res: any) => {
          Swal.fire({
            title: (res.ok) ? '' : 'เกิดข้อผิดพลาด!',
            icon: (res.ok) ? 'success' : 'error',
            text: res.message,
            showConfirmButton: !res.ok,
            timer: (res.ok) ? 1500 : 0
          }).then(() => {
            if (res.ok) {
              this.getMemberList();
            }
          });
        });
      }
    });
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

}

import { Component, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { IMyDateModel, IAngularMyDpOptions } from '@nodro7/angular-mydatepicker';
import { MainService } from 'src/app/services/main.service';
import Swal from 'sweetalert2';
declare const $: any;

@Component({
  selector: 'app-home',
  templateUrl: './home.component.html',
  styleUrls: ['./home.component.scss']
})
export class HomeComponent {

  @ViewChild('dp') mydp: any;

  public account = this.main.jwtDecode;
  public data: any[] = [];
  public row: any;
  public drugStatus: string = '';
  public followupStatus: string = '';
  public year = new Date().getFullYear();
  public month = 0;
  public department = '';
  public arrYear: any[] = [];
  public arrMonth = ['==ทั้งหมด==', 'มกราคม', 'กุมภาพันธ์', 'มีนาคม', 'เมษายน', 'พฤษภาคม', 'มิถุนายน', 'กรกฎาคม', 'สิงหาคม', 'กันยายน', 'ตุลาคม', 'พฤศจิกายน', 'ธันวาคม'];
  public arrDepartment: any[] = [];
  public acceptDate: IMyDateModel = { isRange: false, singleDate: { jsDate: new Date('') } };
  public acceptBy: string = '';
  public myDatePickerOptions: IAngularMyDpOptions = {
    dateRange: false,
    inline: false,
    dateFormat: 'dd mmm yyyy',
    satHighlight: true
  };
  public accept: 'N' | 'Y' | 'F' = 'N';

  constructor(
    private main: MainService,
    private router: Router
  ) {
    // this.router.events.subscribe((value: any) => {
    //   if (value instanceof NavigationEnd) {
    //     console.log(value.url);
    //     this.accept = (value.url == '/patient/accepted') ? 'Y' : 'N';
    //     this.list(this.year, this.month, this.accept);
    //   }
    // });
    this.accept = (this.router.url == '/patient/accepted') ? 'Y' : (this.router.url == '/patient/not-accept') ? 'N' : 'F';
    this.list(this.year, this.month, this.accept);
    for (let i = 0; i < 5; i++) {
      this.arrYear.push(this.year - i);
    }
  }

  list(year: any, month: any, accept: 'N' | 'Y' | 'F') {
    this.data = [];
    this.main.get(`patient/list/${year}/${month}/${accept}`).then((res: any) => {
      this.data = (res.ok) ? res.data : [];
      this.arrDepartment = (res.ok) ? res.department : [];
      $(document).ready(() => {
        $('#repoTable').DataTable({
          scrollX: false,
          lengthMenu: [20, 50, 100],
          autoWidth: false,
          destroy: true,
          searching: true
        });
      });
    });
  }

  saveAccept(data: any) {
    if (this.acceptDate && this.acceptBy) {
      let hospmain = data.hospmain;
      let hn = data.hn;
      let vn = data.vn;
      let year = this.acceptDate.singleDate?.date?.year;
      let month = this.acceptDate.singleDate?.date?.month;
      let day = this.acceptDate.singleDate?.date?.day;
      data = { dwh_hospcode: data.dwh_hospcode, accept_date: `${year}-${month}-${day}`, accept_by: this.acceptBy, accept: 'Y' }
      this.main.put(`patient/accept/${hospmain}/${hn}/${vn}`, data).then((res: any) => {
        if (res.ok) {
          this.list(this.year, this.month, this.accept);
          this.successAlert(res.message);
          $('#modal-accept').modal('hide');
        } else {
          this.errorAlert(res.message);
        }
      });
    } else {
      this.warningAlert('กรุณากรอกลงวันที่นัดหมาย และ ชื่อผู้นัดหมายด้วย!');
    }
  }

  saveDrugStatus(data: any) {
    let hospmain = data.hospmain;
    let hn = data.hn;
    let vn = data.vn;
    data = { drug_status: this.drugStatus, dwh_hospcode: data.dwh_hospcode }
    this.main.put(`patient/drugStatus/${hospmain}/${hn}/${vn}`, data).then((res: any) => {
      // this.list(this.year, this.month, this.accept);
      // $('#modal-drug-status').modal('hide');
      if (res.ok) {
        this.list(this.year, this.month, this.accept);
        this.successAlert(res.message);
        $('#modal-drug-status').modal('hide');
      } else {
        this.errorAlert(res.message);
      }
    });
  }

  saveFollowUpStatus(data: any) {
    let hospmain = data.hospmain;
    let hn = data.hn;
    let vn = data.vn;
    data = { followup_status: this.followupStatus, dwh_hospcode: data.dwh_hospcode }
    this.main.put(`patient/followUpStatus/${hospmain}/${hn}/${vn}`, data).then((res: any) => {
      if (res.ok) {
        this.list(this.year, this.month, this.accept);
        this.successAlert(res.message);
        $('#modal-follow-up-status').modal('hide');
      } else {
        this.errorAlert(res.message);
      }
    });
  }

  onDetail(data: any) {
    // $('#modal-detail').modal({ backdrop: 'static', keyboard: false });
    $('#modal-detail').modal();
    this.row = data;
  }

  onAccept(data: any) {
    this.row = data;
    if (data.accept == 'Y') {
      this.acceptDate = { isRange: false, singleDate: { jsDate: new Date(data.accept_date) } };
      this.acceptBy = data.accept_by;
    } else {
      this.mydp.clearDate();
      this.acceptBy = (this.account.hospcode == data?.dwh_hospcode && this.account.fname) ? (this.account.pname + this.account.fname + ' ' + this.account.lname) : '';
    }
    $('#modal-accept').modal();
    setTimeout(() => $('#accept-by').focus(), 500);
  }

  onDrugStatus(data: any) {
    $('#modal-drug-status').modal();
    this.row = data;
    this.drugStatus = data.drug_status;
  }

  onFollowUpStatus(data: any) {
    $('#modal-follow-up-status').modal();
    this.row = data;
    this.followupStatus = data.followup_status;
  }

  selectDate() {
    this.list(this.year, this.month, this.accept);
  }

  icd(txt: string) {
    let arr: any[] = (txt) ? txt.split(',') : [];
    if (txt) {
      for (let i = 0; i < arr.length; i++) {
        arr[i] = `${i + 1}. ` + arr[i].split('|').join(' ');
      }
    }
    return arr.join('<br />');
  }

  drug(txt: string) {
    return (txt) ? txt.split(',') : [];
  }

  successAlert(msg: string) {
    Swal.fire({
      icon: 'success',
      title: msg,
      showConfirmButton: false,
      timer: 1500
    });
  }

  errorAlert(msg: string) {
    Swal.fire({
      icon: 'error',
      text: msg,
      allowOutsideClick: false
    });
  }

  warningAlert(msg: string) {
    Swal.fire({
      icon: 'warning',
      text: msg,
      allowOutsideClick: false
    });
  }

  printSection() {
    const printContents = $('#modal-detail').html();
    if (printContents) {
      const popupWin = window.open('', '_blank', 'top=0,left=0,height=100%,width=auto');
      if (popupWin) {
        popupWin.document.open();
        popupWin.document.write(`
          <html>
            <head>
              <title>ระบบ Drug Refer Back จ.ชัยภูมิ</title>
              <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@3.3.7/dist/css/bootstrap.min.css" />
              <style>
                .headercenter th {
                  text-align: center;
                  vertical-align: middle;
                }
                .headercenter td {
                  vertical-align: middle;
                }
              </style>
            </head>
            <body onload="window.print();window.close()">${printContents}</body>
          </html>`
        );
        popupWin.document.close();
      }
    }
  }

}

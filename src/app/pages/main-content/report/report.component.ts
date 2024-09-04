import { Component } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { MainService } from 'src/app/services/main.service';
declare const $: any;

@Component({
  selector: 'app-report',
  templateUrl: './report.component.html',
  styleUrls: ['./report.component.scss']
})
export class ReportComponent {

  public reportName: string = '';
  public menuId: string = '1';
  public year = new Date().getFullYear();
  public arrYear: any[] = [];
  public report: any[] = [];

  constructor(
    private main: MainService,
    private route: ActivatedRoute
  ) {
    this.route.paramMap.subscribe(params => {
      this.menuId = params.get('menuId') || '1';
      this.getReport(this.menuId, this.year);
    });
    for (let i = 0; i < 3; i++) {
      this.arrYear.push(this.year - i);
    }
  }

  getReport(menuId: string, bYear: number) {
    this.report = [];
    this.main.get(`report/bYear/${bYear}/menu/${menuId}`).then((res: any) => {
      this.report = (res.ok) ? res.data : [];
      this.reportName = res.name;
      $(document).ready(() => {
        $('#repoTable').DataTable({
          scrollX: false,
          lengthMenu: [20, 50, 100],
          autoWidth: false,
          destroy: true,
          searching: true
        });
      });
    }).catch((err: any) => {
      console.log(err);
    });
  }

}

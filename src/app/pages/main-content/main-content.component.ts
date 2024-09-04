import { Component } from '@angular/core';
import { MainService } from 'src/app/services/main.service';

@Component({
  selector: 'app-main-content',
  templateUrl: './main-content.component.html',
  styleUrls: ['./main-content.component.scss']
})
export class MainContentComponent {

  public account = this.main.jwtDecode;

  constructor(
    private main: MainService
  ) {
    document.body.className = 'hold-transition skin-green sidebar-mini fixed';
  }

  onLogout() {
    this.main.logout();
  }

}

import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-sh-details2',
  templateUrl: './sh-details2.component.html',
  styleUrls: ['./sh-details2.component.scss'],
})
export class ShDetails2Component {
  @Input() row: any;

  drug(txt: string) {
    return txt ? txt.split(',') : [];
  }
}

import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-sh-details0',
  templateUrl: './sh-details0.component.html',
  styleUrls: ['./sh-details0.component.scss'],
})
export class ShDetails0Component {
  @Input() row: any;

  icd(txt: string) {
    let arr: any[] = txt ? txt.split(',') : [];
    if (txt) {
      for (let i = 0; i < arr.length; i++) {
        arr[i] = `${i + 1}. ` + arr[i].split('|').join(' ');
      }
    }
    return arr.join('<br />');
  }

  drug(txt: string) {
    return txt ? txt.split(',') : [];
  }
}

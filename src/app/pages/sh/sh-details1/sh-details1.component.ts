import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-sh-details1',
  templateUrl: './sh-details1.component.html',
  styleUrls: ['./sh-details1.component.scss'],
})
export class ShDetails1Component {
  @Input() row: any;

  drug(txt: string) {
    return txt ? txt.split(',') : [];
  }
}

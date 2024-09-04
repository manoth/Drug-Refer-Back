import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-sh-details3',
  templateUrl: './sh-details3.component.html',
  styleUrls: ['./sh-details3.component.scss']
})
export class ShDetails3Component {
  @Input() row: any;

  lab(txt: string) {
    return txt ? txt.split(',') : [];
  }
}

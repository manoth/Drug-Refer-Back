import { Pipe, PipeTransform } from '@angular/core';
import * as _ from 'lodash';
@Pipe({
    name: 'sumDataOne'
})
export class SumDataOnePipe implements PipeTransform {

    transform(value: any, fdName?: any, tf?: number): any {
        try {
            const total = _.sumBy(value, (o: any) => Number(o[fdName]));
            return total.toFixed(tf);
        } catch (error) {
            return 'ไม่ระบุ';
        }

    }
}


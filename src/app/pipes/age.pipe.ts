import { Pipe, PipeTransform } from '@angular/core';
import * as moment from 'moment';

@Pipe({
    name: 'age',
    pure: false
})
export class AgePipe implements PipeTransform {

    transform(value: any): any {
        if (!isNaN(Date.parse(value))) {
            try {
                const now = new Date(value);
                const setNowDate = moment();
                const setdayBirth = moment.utc(now).format('YYYY-MM-DD');
                // const setdayBirth = now.getFullYear() + '-' + (+now.getMonth() + 1) + '-' + now.getDay();
                const yearData = setNowDate.diff(setdayBirth, 'years', true);
                const yearReal = setNowDate.diff(setdayBirth, 'years');
                const monthDiff = Math.floor((yearData - yearReal) * 12);
                const rs = yearReal + ' ปี ' + monthDiff + ' เดือน ';
                return rs;
            } catch (error) {
                return 'ไม่ระบุวันที่';
            }
        } else {
            return 'ไม่ระบุวันที่';
        }
    }

}
import { Injectable } from '@angular/core';
import { HttpRequest, HttpHandler, HttpEvent, HttpInterceptor } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';

import { MainService } from '../services/main.service';

@Injectable()
export class ErrorInterceptor implements HttpInterceptor {
    constructor(private main: MainService) { }
    intercept(request: HttpRequest<any>, next: HttpHandler): Observable<HttpEvent<any>> {
        return next.handle(request).pipe(catchError(err => {
            if (err.status === 401) {
                this.main.logout();
                location.reload();
            }
            const error = err.error.message || err.statusText;
            return throwError(error);
        }))
    }
}
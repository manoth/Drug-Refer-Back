import { Injectable, Inject } from '@angular/core';
import { CanActivate, ActivatedRouteSnapshot, RouterStateSnapshot } from '@angular/router';
import { MainService } from '../services/main.service';

@Injectable({ providedIn: 'root' })
export class Auth1Guard implements CanActivate {
    constructor(
        private main: MainService
    ) { }
    canActivate(route: ActivatedRouteSnapshot, state: RouterStateSnapshot) {
        if (!this.main.auth1) {
            this.main.logout();
            return false;
        } else {
            return true;
        }
    }
}

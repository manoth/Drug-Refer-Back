import { Injectable, Inject } from '@angular/core';
import { CanActivate, ActivatedRouteSnapshot, RouterStateSnapshot, Router } from '@angular/router';
import { MainService } from '../services/main.service';

@Injectable({ providedIn: 'root' })
export class Auth2Guard implements CanActivate {
    constructor(
        private router: Router,
        private main: MainService
    ) { }
    canActivate(route: ActivatedRouteSnapshot, state: RouterStateSnapshot) {
        if (!this.main.auth2) {
            this.router.navigate(['/']);
            return false;
        } else {
            return true;
        }
    }
}

from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import SubClass, Brand
from portal import loggedin_required
from sqlalchemy.exc import IntegrityError 
from flask import jsonify

# Blueprint for Subclass logic
subclass_bp = Blueprint('subclass', __name__)

@subclass_bp.route('/api/search_groups')
@loggedin_required()
def search_groups():
    """API to provide autocomplete suggestions for Product Groups."""
    query = request.args.get('q', '').strip().upper()
    if not query:
        return jsonify([])
    
    # Searches for matches in the Brand table's product_group column
    brands = Brand.query.filter(Brand.product_group.like(f"%{query}%")).limit(10).all()
    
    # Return as JSON for the frontend to consume
    return jsonify([{"code": b.product_group, "name": b.brand_name} for b in brands])

@subclass_bp.route('/admin/add_subclass', methods=['GET', 'POST'])
@loggedin_required()
def add_subclass():
    """Route to add a new subclass entry with Composite Key and Foreign Key safety."""
    if request.method == 'POST':
        pg = request.form.get('product_group', '').strip().upper()
        code = request.form.get('subclass_code', '').strip().zfill(3)
        name = request.form.get('subclass_name', '').strip().upper()

        try:
            new_sub = SubClass(
                product_group=pg,
                subclass_code=code,
                subclass_name=name
            )
            
            db.session.add(new_sub)
            db.session.commit()
            
            flash(f"Successfully added Subclass: {name} under Group: {pg}", "success")
            return redirect(url_for('admin_management', _anchor='subclass'))
            
        except IntegrityError:
            db.session.rollback()
            # This triggers if the combo already exists OR the Product Group isn't in the Brand table
            flash(f"Error: The Subclass code '{code}' already exists for Group '{pg}' OR the Product Group is not registered.", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Database Error: {str(e)}", "danger")
            
    return render_template('add_subclass.html')

@subclass_bp.route('/admin/edit_subclass/<group>/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_subclass(group, code):
    """Route to update an existing subclass using composite keys with conflict checks."""
    sub = SubClass.query.filter_by(product_group=group, subclass_code=code).first_or_404()
    
    if request.method == 'POST':
        new_pg = request.form.get('product_group', '').strip().upper()
        new_code = request.form.get('subclass_code', '').strip().zfill(3)
        new_name = request.form.get('subclass_name', '').strip().upper()

        try:
            sub.product_group = new_pg
            sub.subclass_code = new_code
            sub.subclass_name = new_name
            
            db.session.commit()
            flash(f"Subclass '{new_name}' updated successfully!", "success")
            return redirect(url_for('admin_management', _anchor='subclass'))
            
        except IntegrityError:
            db.session.rollback()
            flash("Update Failed: This specific Group/Code combination is already in use.", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating subclass: {str(e)}", "danger")
            
    return render_template('edit_subclass.html', subclass=sub)

@subclass_bp.route('/admin/delete_subclass/<group>/<code>', methods=['POST'])
@loggedin_required()
def delete_subclass(group, code):
    """Route to remove a subclass safely."""
    sub = SubClass.query.filter_by(product_group=group, subclass_code=code).first()
    
    if sub:
        try:
            db.session.delete(sub)
            db.session.commit()
            flash(f"Subclass {code} deleted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Delete Error: {str(e)}", "danger")
    else:
        flash("Record not found or already deleted.", "warning")
        
    return redirect(url_for('admin_management', _anchor='subclass'))